"""Real process faults + two-instance competition + bounded DB retry + short sustained load.
Usage: python scripts/reliability_checks.py --load-seconds 8 --workers 12 --pools 3,10
Requires an existing portable runtime, built JAR and reliability-requirements.txt.
"""
import argparse
import concurrent.futures as futures
import json
import hashlib
import math
import statistics
import threading
import time
import uuid
from datetime import datetime

import pymysql
import requests
from reliability_lab import Lab, wait


def batch(count, work):
    barrier = threading.Barrier(count)
    def run(index):
        barrier.wait()
        return work(index)
    with futures.ThreadPoolExecutor(max_workers=count) as pool:
        return list(pool.map(run, range(count)))


def crash_request(lab, name, future):
    lab.stop(lab.apps[name])  # Only the Popen handle the runner started.
    try:
        future.result(timeout=20)
    except requests.RequestException:
        return
    raise AssertionError('Fault request unexpectedly returned a confirmed response')


def order_faults(lab):
    for stage in ('submit.beforeCommit', 'submit.afterCommit'):
        actor, meal = lab.actor(), lab.meal()
        lab.cart(actor)
        body = lab.payload(meal)
        gate = lab.arm('a', stage, body['requestKey'])
        with futures.ThreadPoolExecutor(1) as pool:
            request = pool.submit(lab.submit, actor, body)
            wait(lambda: lab.reached('a', gate), description=stage)
            order = lab.one('SELECT * FROM orders WHERE user_id=%s AND request_key=%s', (actor['id'], body['requestKey']))
            reserved = lab.scalar('SELECT reserved_count FROM meal_session WHERE id=%s', (meal['id'],))
            cart = lab.scalar('SELECT COALESCE(SUM(number),0) FROM shopping_cart WHERE user_id=%s', (actor['id'],))
            before = stage.endswith('beforeCommit')
            lab.check(stage + ': observer sees only committed data',
                      (order is None and reserved == 0 and cart == 1) if before else (order is not None and reserved == 1 and cart == 0),
                      {'orderVisible': order is not None, 'reserved': reserved, 'cartQuantity': cart})
            crash_request(lab, 'a', request)
        if before:
            wait(lambda: lab.scalar('SELECT COUNT(*) FROM information_schema.INNODB_TRX WHERE trx_mysql_thread_id IN '
                                    '(SELECT id FROM information_schema.PROCESSLIST WHERE user=%s)', (lab.db_user,), root=True) == 0,
                 description='crashed transaction rollback')
            lab.check('real pre-commit process crash rolls back all writes',
                      lab.scalar('SELECT COUNT(*) FROM orders WHERE user_id=%s', (actor['id'],)) == 0
                      and lab.scalar('SELECT reserved_count FROM meal_session WHERE id=%s', (meal['id'],)) == 0
                      and lab.scalar('SELECT SUM(number) FROM shopping_cart WHERE user_id=%s', (actor['id'],)) == 1)
        lab.start_app('a')
        replay = lab.submit(actor, body, 'b')
        again = lab.submit(actor, body, 'a')
        lab.check(stage + ': restart and cross-instance same-key retry return one order',
                  replay['id'] == again['id'] and (before or replay['id'] == order['id'])
                  and lab.scalar('SELECT COUNT(*) FROM orders WHERE user_id=%s', (actor['id'],)) == 1)
        lab.cancel(actor, replay)


def refund_faults(lab):
    _, _, processing_order, processing_task = lab.refunded_order()
    lab.fault(processing_order, 0, 'PROCESSING')
    lab.run_refund(processing_task)
    lab.check('PROCESSING provider response persists submitted intent for later query',
              lab.task(processing_order)['status'] == 'PENDING' and lab.task(processing_order)['provider_submitted'] == 1)

    actor, meal, order, task = lab.refunded_order()
    lab.fault(order, 8000)  # Merchant COMMIT -> response beyond the client's six-second timeout.
    lab.run_refund(task)
    ledger = lab.ledger(order)
    lab.check('merchant committed refund but transport timeout leaves recoverable local intent',
              ledger['actualRefunds'] == 1 and ledger['refundCalls'] == 1
              and lab.task(order)['status'] == 'PENDING' and lab.task(order)['failures'] == 1, ledger)
    wait(lambda: lab.due(order), description='real retry backoff')
    lab.run_refund(task, 'b')
    lab.check('timeout retry reuses refund number: repeated calls, one actual refund',
              lab.task(order)['status'] == 'SUCCEEDED' and lab.ledger(order)['actualRefunds'] == 1
              and lab.ledger(order)['refundCalls'] == 1 and lab.ledger(order)['queryCalls'] == 1, lab.ledger(order))

    actor, meal, order, task = lab.refunded_order()
    gate = lab.arm('a', 'refund.beforeCommit', task['refund_number'])
    with futures.ThreadPoolExecutor(1) as pool:
        request = pool.submit(lab.run_refund, task)
        wait(lambda: lab.reached('a', gate), description='refund local confirmation before commit')
        row = lab.one('SELECT pay_status,refund_status FROM orders WHERE id=%s', (order['id'],))
        lab.check('external refund committed while local confirmation writes remain uncommitted',
                  lab.ledger(order)['actualRefunds'] == 1 and row == {'pay_status': 1, 'refund_status': 'PENDING'}
                  and lab.task(order)['status'] == 'PROCESSING', lab.ledger(order))
        crash_request(lab, 'a', request)
    lab.stop(lab.merchant)
    lab.start_provider()  # Same SQLite file, fresh provider process.
    lab.start_app('a')
    wait(lambda: lab.due(order), description='crashed worker lease expires')
    lab.run_refund(task, 'b')
    lab.check('app and merchant restart recover committed external refund without double refund',
              lab.task(order)['status'] == 'SUCCEEDED' and lab.task(order)['attempts'] == 2
              and lab.ledger(order)['actualRefunds'] == 1 and lab.ledger(order)['refundCalls'] == 1
              and lab.ledger(order)['queryCalls'] == 1, lab.ledger(order))

    actor, meal, order, task = lab.refunded_order()
    lab.fault(order, 4500, 'FAILED')  # Old worker arrives late with failure after a new owner succeeds.
    with futures.ThreadPoolExecutor(1) as pool:
        old = pool.submit(lab.run_refund, task)
        wait(lambda: lab.ledger(order)['actualRefunds'] == 1, description='old worker external write')
        old_token = lab.task(order)['lease_token']
        wait(lambda: lab.due(order), description='real lease expiry')
        lab.run_refund(task, 'b')
        current = lab.task(order)
        assert current['lease_token'] != old_token and current['status'] == 'SUCCEEDED'
        old.result(timeout=20)
    final = lab.task(order)
    lab.check('late old lease token cannot overwrite new success with failure',
              final == current and final['failures'] == 0 and lab.ledger(order)['refundCalls'] == 1
              and lab.ledger(order)['queryCalls'] == 1
              and lab.ledger(order)['actualRefunds'] == 1,
              dict(lab.ledger(order), attempts=final['attempts'], failures=final['failures'], staleWriteRejected=True))
    wait(lambda: lab.due(processing_order), timeout=65, description='provider query poll becomes due')
    lab.run_refund(processing_task, 'b')
    lab.check('submitted provider intent recovers by query without another refund POST',
              lab.task(processing_order)['status'] == 'SUCCEEDED'
              and lab.ledger(processing_order)['refundCalls'] == 1 and lab.ledger(processing_order)['queryCalls'] == 1,
              lab.ledger(processing_order))


def protocol_and_competition(lab):
    actor, meal = lab.actor(), lab.meal()
    lab.cart(actor)
    body = lab.payload(meal)
    body.update(amount=.01, packAmount=0)
    responses = batch(50, lambda i: lab.call('a' if i % 2 else 'b', 'POST', '/user/order/submit', body, actor=actor, expected=None))
    order = responses[0]['data']
    lab.check('two apps: 50 same-key requests persist one order and reserve once',
              all(row['_http'] == 200 and row['code'] == 1 and row['data']['id'] == order['id'] for row in responses)
              and lab.scalar('SELECT COUNT(*) FROM orders WHERE user_id=%s', (actor['id'],)) == 1
              and lab.scalar('SELECT reserved_count FROM meal_session WHERE id=%s', (meal['id'],)) == 1)
    lab.check('server amount ignores client pricing fields', str(order['orderAmount']) == str(lab.dish['price'] + 1)
              or float(order['orderAmount']) == float(lab.dish['price'] + 1))
    conflict = dict(body, contactName='另一个人')
    lab.call('b', 'POST', '/user/order/submit', conflict, actor=actor, expected=0)
    lab.check('same key with different semantic parameters is rejected')
    lab.cart(actor, 'b')
    original_price = lab.dish['price']
    try:
        lab.sql('UPDATE dish SET price=price+1 WHERE id=%s', (lab.dish['id'],))
        replay = lab.submit(actor, body, 'b')
        lab.check('same key ignores new cart and later server price; preserves both original result and new cart',
                  replay == order and lab.scalar('SELECT SUM(number) FROM shopping_cart WHERE user_id=%s', (actor['id'],)) == 1)
    finally:
        lab.sql('UPDATE dish SET price=%s WHERE id=%s', (original_price, lab.dish['id']))
    new = lab.submit(actor, lab.payload(meal), 'b')
    lab.check('same user can place another order in the same meal using a new key', new['id'] != order['id'])
    lab.cancel(actor, order)
    lab.cancel(actor, new)

    actor, meal = lab.actor(), lab.meal()
    lab.cart(actor)
    different = batch(10, lambda i: lab.call('a' if i % 2 else 'b', 'POST', '/user/order/submit', lab.payload(meal), actor=actor, expected=None))
    lab.check('different keys cannot simultaneously consume one cart',
              sum(row.get('code') == 1 for row in different) == 1
              and all(row['_http'] == 200 and row.get('code') in (0, 1) for row in different))
    lab.cancel(actor, next(row['data'] for row in different if row['code'] == 1))
    lab.cart(actor)
    body = lab.payload(meal)
    mixed = batch(2, lambda i: lab.call('a' if i == 0 else 'b', 'POST', '/user/shoppingCart/add' if i == 0 else '/user/order/submit',
                                      {'dishId': lab.dish['id']} if i == 0 else body, actor=actor))
    order = mixed[1]['data']
    ordered = lab.scalar('SELECT SUM(number) FROM order_detail WHERE order_id=%s', (order['id'],))
    remaining = lab.scalar('SELECT COALESCE(SUM(number),0) FROM shopping_cart WHERE user_id=%s', (actor['id'],))
    lab.check('cross-instance add and checkout conserve cart quantities', ordered + remaining == 2,
              {'orderedQuantity': int(ordered), 'remainingCartQuantity': int(remaining)})
    lab.cancel(actor, order)

    meal = lab.meal(5)
    actors = [lab.actor() for _ in range(20)]
    for actor in actors:
        lab.cart(actor)
    rows = batch(20, lambda i: lab.call('a' if i % 2 else 'b', 'POST', '/user/order/submit', lab.payload(meal), actor=actors[i], expected=None))
    lab.check('two apps: 20 users contest five portions; losers retain carts',
              sum(row.get('code') == 1 for row in rows) == 5 and sum(row.get('code') == 0 for row in rows) == 15
              and all(row['_http'] == 200 for row in rows) and lab.scalar('SELECT reserved_count FROM meal_session WHERE id=%s', (meal['id'],)) == 5
              and all(lab.scalar('SELECT COUNT(*) FROM shopping_cart WHERE user_id=%s', (actor['id'],)) == (0 if row['code'] == 1 else 1)
                      for actor, row in zip(actors, rows)), {'success': 5, 'businessRejections': 15, 'systemErrors': 0})
    for actor, row in zip(actors, rows):
        if row['code'] == 1:
            lab.cancel(actor, row['data'])

    actor, meal = lab.actor(), lab.meal()
    lab.cart(actor)
    order = lab.submit(actor, lab.payload(meal))
    def compete(index):
        name = 'a' if index % 2 else 'b'
        if index % 3 == 0:
            return lab.pay(actor, order, name)
        if index % 3 == 1:
            return lab.cancel(actor, order, name)
        return lab.call(name, 'POST', '/admin/local/refunds/run', admin=True)
    batch(60, compete)
    task = lab.task(order)
    if task['status'] != 'SUCCEEDED':
        wait(lambda: lab.due(order), description='competing refund becomes due')
        batch(20, lambda i: lab.run_refund(task, 'a' if i % 2 else 'b'))
    row = lab.one('SELECT status,pay_status,refund_status FROM orders WHERE id=%s', (order['id'],))
    lab.check('duplicate payment/cancel/recovery competition preserves state and releases once',
              row == {'status': 6, 'pay_status': 2, 'refund_status': 'SUCCEEDED'}
              and lab.scalar('SELECT reserved_count FROM meal_session WHERE id=%s', (meal['id'],)) == 0
              and lab.scalar('SELECT COUNT(*) FROM refund_task WHERE order_id=%s', (order['id'],)) == 1
              and lab.ledger(order)['actualRefunds'] == 1, lab.ledger(order))
    lab.check('two apps: conditional claim has one owner for each unexpired lease', lab.task(order)['attempts'] == 1,
              {'attempts': lab.task(order)['attempts']})


def database_retry(lab):
    before = lab.metrics('a')['dbRetries']
    actor, meal = lab.actor(), lab.meal()
    lab.cart(actor)
    body = lab.payload(meal)
    with lab.db() as blocker, blocker.cursor() as cursor, futures.ThreadPoolExecutor(1) as pool:
        blocker.begin()
        cursor.execute('SELECT id FROM meal_session WHERE id=%s FOR UPDATE', (meal['id'],))
        pending = pool.submit(lab.submit, actor, body)
        wait(lambda: lab.metrics('a')['dbRetries'] > before, timeout=8, description='real MySQL 1205 retry')
        blocker.commit()
        order = pending.result(timeout=20)
    lab.check('real lock timeout retries the full checkout transaction with the original key',
              lab.metrics('a')['dbRetries'] > before and lab.scalar('SELECT COUNT(*) FROM orders WHERE user_id=%s AND request_key=%s',
                                                                  (actor['id'], body['requestKey'])) == 1,
              {'retryDelta': lab.metrics('a')['dbRetries'] - before})
    lab.cancel(actor, order)

    before = lab.metrics('a')['dbRetries']
    exhausted = lab.metrics('a')['dbRetryExhausted']
    actor, meal = lab.actor(), lab.meal()
    lab.cart(actor)
    body = lab.payload(meal)
    with lab.db() as blocker, blocker.cursor() as cursor:
        blocker.begin()
        cursor.execute('SELECT id FROM meal_session WHERE id=%s FOR UPDATE', (meal['id'],))
        result = lab.call('a', 'POST', '/user/order/submit', body, actor=actor, expected=None)
        lab.check('retry exhaustion returns system 503, rolls back and keeps cart/capacity intact',
                  result['_http'] == 503 and lab.metrics('a')['dbRetries'] - before == 2
                  and lab.metrics('a')['dbRetryExhausted'] == exhausted + 1
                  and lab.scalar('SELECT COUNT(*) FROM orders WHERE user_id=%s', (actor['id'],)) == 0
                  and lab.scalar('SELECT SUM(number) FROM shopping_cart WHERE user_id=%s', (actor['id'],)) == 1)
        blocker.commit()
    order = lab.submit(actor, body, 'b')
    lab.check('original key succeeds after an exhausted retry and contention clears', order['id'] is not None)
    lab.cancel(actor, order)

    before, deadlocks = lab.metrics('a')['dbRetries'], lab.db_metrics().get('lock_deadlocks')
    gate = lab.arm('a', 'dbProbe.afterWrite', 'deadlock')
    with lab.db() as blocker, blocker.cursor() as cursor, futures.ThreadPoolExecutor(1) as pool:
        blocker.begin()
        cursor.execute('UPDATE reliability_probe SET value=value+1 WHERE id BETWEEN 2 AND 20')
        pending = pool.submit(lab.call, 'a', 'POST', '/admin/local/reliability/db-probe', {'key': 'deadlock'}, admin=True)
        wait(lambda: lab.reached('a', gate), description='transaction wrote first probe row')
        lab.release('a', gate)
        wait(lambda: any(row['objectName'] == 'reliability_probe' for row in lab.waits()), description='second lock wait')
        cursor.execute('UPDATE reliability_probe SET value=value+10 WHERE id=1')  # Forms a real InnoDB wait cycle.
        blocker.commit()
        pending.result(timeout=20)
    lab.check('real InnoDB deadlock rolls back earlier write and retries in a fresh transaction',
              lab.metrics('a')['dbRetries'] > before and (deadlocks is None or lab.db_metrics()['lock_deadlocks'] > deadlocks)
              and lab.scalar('SELECT value FROM reliability_probe WHERE id=1') == 11
              and lab.scalar('SELECT value FROM reliability_probe WHERE id=2') == 2,
              {'retryDelta': lab.metrics('a')['dbRetries'] - before, 'deadlockDelta': None if deadlocks is None else lab.db_metrics()['lock_deadlocks'] - deadlocks,
               'row1': 11, 'row2': 2})


def cancellation_lock_regression(lab):
    actor, meal = lab.actor(), lab.meal()
    lab.cart(actor)
    order = lab.submit(actor, lab.payload(meal))
    with lab.db() as connection, connection.cursor() as cursor:
        connection.begin()
        cursor.execute('UPDATE orders SET status=6 WHERE id=%s', (order['id'],))
        locks = lab.sql("SELECT LOCK_MODE FROM performance_schema.data_locks WHERE OBJECT_SCHEMA=%s "
                        "AND OBJECT_NAME='meal_session' AND LOCK_TYPE='RECORD'", (lab.schema,), root=True)
        lab.check('isolated raw child-status update demonstrates parent FK shared lock', any('S' in row['LOCK_MODE'].split(',') for row in locks))
        connection.rollback()  # Do not leave the low-level experiment as a business cancellation.
    trigger = '`' + lab.schema + '`.`reliability_cancel_failure`'
    # With binlog enabled MySQL requires administrative permission to create a trigger.
    # Scope the DDL to the generated test schema; never grant SUPER or change global settings.
    lab.sql('CREATE TRIGGER ' + trigger + ' BEFORE UPDATE ON `' + lab.schema + '`.orders FOR EACH ROW BEGIN '
            'IF OLD.id=' + str(order['id']) + " AND NEW.status=6 THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='test cancel write failure'; END IF; END", root=True)
    try:
        result = lab.call('a', 'PUT', '/user/order/cancel/' + str(order['id']), actor=actor, expected=None)
        lab.check('capacity-first cancellation still rolls back when later order update fails',
                  result['_http'] == 503 and lab.scalar('SELECT status FROM orders WHERE id=%s', (order['id'],)) == 1
                  and lab.scalar('SELECT reserved_count FROM meal_session WHERE id=%s', (meal['id'],)) == 1
                  and lab.scalar('SELECT COUNT(*) FROM refund_task WHERE order_id=%s', (order['id'],)) == 0)
    finally:
        lab.sql('DROP TRIGGER ' + trigger, root=True)
    lab.cancel(actor, order)
    lab.check('normal cancellation after injected failure releases one portion',
              lab.scalar('SELECT reserved_count FROM meal_session WHERE id=%s', (meal['id'],)) == 0)


def percentile(values, value):
    ordered = sorted(values)
    return round(ordered[max(0, math.ceil(value * len(ordered)) - 1)], 2) if ordered else None


def sustained(lab, scenario, seconds, workers):
    actors = [lab.actor() for _ in range(1 if scenario == 'same_user_replay' else workers)]
    meals = [lab.meal(max(2, workers // 2)) for _ in range(workers if scenario == 'spread_sessions' else 1)]
    if scenario == 'same_user_replay':
        lab.cart(actors[0])
        body = lab.payload(meals[0])
        original = lab.submit(actors[0], body)
    else:
        original = None
    stop = threading.Event()
    samples = []
    network_samples = []
    metric_errors = []
    db_start = lab.db_metrics()
    pool_start = {name: lab.metrics(name) for name in ('a', 'b')}
    started = time.perf_counter()
    deadline = started + seconds
    def sample():
        next_progress = started + 30
        while not stop.is_set():
            try:
                samples.append({'locks': lab.waits(), 'pools': [lab.metrics(name) for name in ('a', 'b')]})
            except Exception as error:
                metric_errors.append(type(error).__name__)
            if time.perf_counter() >= next_progress:
                print('LOAD ' + scenario + ': elapsed ' + str(round(time.perf_counter() - started))
                      + '/' + str(seconds) + ' seconds', flush=True)
                next_progress += 30
            stop.wait(.2)
        lab.release_thread_session()
    observer = threading.Thread(target=sample, daemon=True)
    observer.start()
    def network_sample():
        from network_observation import snapshot
        while not stop.is_set():
            network_samples.append({'elapsedSeconds': round(time.perf_counter() - started, 2),
                                    **snapshot({lab.ports['a'], lab.ports['b']})})
            stop.wait(.5)
    network_observer = threading.Thread(target=network_sample, daemon=True) if lab.network_diagnostics else None
    if network_observer:
        network_observer.start()
    def worker(index):
        name = 'a' if index % 2 else 'b'
        actor = actors[0] if scenario == 'same_user_replay' else actors[index]
        meal = meals[index] if scenario == 'spread_sessions' else meals[0]
        outcomes, all_http, created, has_cart, errors = [], 0, [], False, []
        phase = None
        while time.perf_counter() < deadline:
            try:
                if scenario != 'same_user_replay' and not has_cart:
                    phase = 'cart'
                    all_http += 1
                    lab.cart(actor, name)
                    has_cart = True
                request = body if scenario == 'same_user_replay' else lab.payload(meal)
                begin = time.perf_counter()
                phase = 'checkout'
                all_http += 1
                result = lab.call(name, 'POST', '/user/order/submit', request, actor=actor, expected=None)
                kind = 'success' if result['_http'] == 200 and result.get('code') == 1 else (
                    'business_rejection' if result['_http'] == 200 and result.get('code') == 0 else 'system_error')
                outcomes.append((kind, (time.perf_counter() - begin) * 1000))
                if kind == 'system_error':
                    errors.append({'phase': phase, 'http': result['_http']})
                    lab.capture_failure(scenario)
                    break  # Retain request/cart instead of generating a new key after an uncertain failure.
                if kind == 'success':
                    if scenario == 'same_user_replay':
                        assert result['data']['id'] == original['id']
                    else:
                        has_cart = False
                        created.append(result['data']['id'])
                        phase = 'cancel'
                        all_http += 1
                        lab.cancel(actor, result['data'], name)
            except Exception as error:
                if phase == 'checkout':
                    outcomes.append(('system_error', (time.perf_counter() - begin) * 1000))
                errors.append({'phase': phase, 'exception': type(error).__name__, 'detail': str(error)[:400]})
                lab.capture_failure(scenario)
                break
        closes = getattr(lab.http, 'connection_closes', 0)
        lab.release_thread_session()
        return outcomes, all_http, created, errors, closes
    try:
        with futures.ThreadPoolExecutor(workers) as pool:
            results = list(pool.map(worker, range(workers)))
    finally:
        stop.set()
        observer.join(timeout=30)
        if observer.is_alive():
            metric_errors.append('observer did not stop within 30 seconds')
        if network_observer:
            network_observer.join(timeout=2)
    elapsed = time.perf_counter() - started
    db_end = lab.db_metrics()
    pool_end = {name: lab.metrics(name) for name in ('a', 'b')}
    outcomes = [item for result in results for item in result[0]]
    latencies = [value for kind, value in outcomes]
    counts = {kind: sum(item[0] == kind for item in outcomes) for kind in ('success', 'business_rejection', 'system_error')}
    lock_peak = {}
    for sample in samples:
        for row in sample['locks']:
            key = str(row['objectName']) + '/' + str(row['indexName'])
            lock_peak[key] = max(lock_peak.get(key, 0), row['waits'])
    result = {'scenario': scenario, 'poolPerInstance': lab.pool, 'workers': workers,
              'requestedSeconds': seconds, 'elapsedSeconds': round(elapsed, 2), 'outcomes': counts,
              'checkoutRequestsPerSecond': round(len(outcomes) / elapsed, 2),
              'successfulCheckoutsPerSecond': round(counts['success'] / elapsed, 2),
              'allBusinessHttpRequestsPerSecond': round(sum(item[1] for item in results) / elapsed, 2),
              'connectionCloseResponses': sum(item[4] for item in results),
              'checkoutP50Ms': percentile(latencies, .5), 'checkoutP95Ms': percentile(latencies, .95),
              'checkoutP99Ms': percentile(latencies, .99),
              'successP95Ms': percentile([value for kind, value in outcomes if kind == 'success'], .95),
              'businessRejectionP95Ms': percentile([value for kind, value in outcomes if kind == 'business_rejection'], .95),
              'dbGlobalDelta': {key: db_end[key] - value for key, value in db_start.items() if key != 'Innodb_row_lock_current_waits'},
              'schemaLockWaitPeak': lock_peak,
              'poolActivePeak': max((value['poolActive'] for sample in samples for value in sample['pools']), default=0),
              'poolWaitThreadsPeak': max((value['poolWaitThreads'] for sample in samples for value in sample['pools']), default=0),
              'poolWaitCountDelta': sum(pool_end[name]['poolWaitCount'] - pool_start[name]['poolWaitCount'] for name in ('a', 'b')),
              'poolWaitMillisDelta': sum(pool_end[name]['poolWaitMillis'] - pool_start[name]['poolWaitMillis'] for name in ('a', 'b')),
              'dbRetryDelta': sum(pool_end[name]['dbRetries'] - pool_start[name]['dbRetries'] for name in ('a', 'b')),
              'metricSamplingErrors': metric_errors, 'workerErrors': [error for item in results for error in item[3]],
              'samples': len(samples)}
    result['clock'] = {'implementation': time.get_clock_info('perf_counter').implementation,
                       'resolutionSeconds': time.get_clock_info('perf_counter').resolution}
    if db_end.get('lock_deadlocks', 0) > db_start.get('lock_deadlocks', 0):
        status = lab.sql('SHOW ENGINE INNODB STATUS', root=True)[0]['Status']
        (lab.directory / ('deadlock-' + scenario + '-pool' + str(lab.pool) + '.txt')).write_text(status, encoding='utf-8')
    with (lab.directory / 'loads.jsonl').open('a', encoding='utf-8') as output:
        output.write(json.dumps(result) + '\n')
    if network_samples:
        with (lab.directory / 'network-samples.jsonl').open('a', encoding='utf-8') as output:
            output.write(json.dumps({'scenario': scenario, 'samples': network_samples}) + '\n')
        result['networkHandshakePendingPeak'] = max(len(item.get('pendingHandshakes', [])) for item in network_samples)
    assert counts['system_error'] == 0 and not result['workerErrors'], result
    ids = [identifier for item in results for identifier in item[2]]
    if scenario == 'same_user_replay':
        lab.check('load same-user replay keeps one committed order',
                  lab.scalar('SELECT COUNT(*) FROM orders WHERE user_id=%s', (actors[0]['id'],)) == 1)
        lab.cancel(actors[0], original)
    else:
        assert len(ids) == len(set(ids)) == counts['success']
        for actor in actors:
            assert lab.scalar('SELECT COUNT(*) FROM orders WHERE user_id=%s AND status<>6', (actor['id'],)) == 0
            assert lab.scalar('SELECT COALESCE(SUM(number),0) FROM shopping_cart WHERE user_id=%s', (actor['id'],)) <= 1
        lab.check('load ' + scenario + ' final orders/carts/capacity reconcile')
    lab.invariants()
    result['finalBusinessDataVerified'] = True
    print(json.dumps(result, ensure_ascii=False), flush=True)
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--load-seconds', type=float, default=8)
    parser.add_argument('--workers', type=int, default=12)
    parser.add_argument('--pools', default='3,10')
    parser.add_argument('--skip-load', action='store_true')
    parser.add_argument('--load-only', action='store_true')
    parser.add_argument('--retry-only', action='store_true')
    args = parser.parse_args()
    pools = [int(item) for item in args.pools.split(',')]
    assert 1 <= args.load_seconds <= 600 and 2 <= args.workers <= 64 and all(1 <= pool <= 32 for pool in pools)
    lab = Lab()
    report = {'date': datetime.now().astimezone().isoformat(), 'status': 'running', 'checks': lab.checks,
              'parameters': vars(args), 'loadRuns': [], 'jarSha256': hashlib.sha256(lab.jar.read_bytes()).hexdigest()}
    try:
        lab.setup()
        report['environment'] = lab.environment_info()
        if not args.load_only:
            if not args.retry_only:
                order_faults(lab)
                refund_faults(lab)
                protocol_and_competition(lab)
                cancellation_lock_regression(lab)
            database_retry(lab)
            lab.invariants()
            lab.check('all isolated business invariants reconcile after faults and races')
        if not args.skip_load:
            for pool in pools:
                if pool != lab.pool:
                    lab.pool = pool
                    lab.restart_app('a')
                    lab.restart_app('b')
                for scenario in ('same_user_replay', 'hot_session', 'spread_sessions'):
                    report['loadRuns'].append(sustained(lab, scenario, args.load_seconds, args.workers))
        report['queryPlans'] = {}
        for name, query, arguments in [
                ('idempotency', 'SELECT * FROM orders WHERE user_id=%s AND request_key=%s FOR UPDATE', (1, 'unused-key')),
                ('cart', 'SELECT * FROM shopping_cart WHERE user_id=%s ORDER BY id FOR UPDATE', (1,)),
                ('refundPending', "SELECT id FROM refund_task WHERE status='PENDING' AND next_retry_time<=NOW() ORDER BY id LIMIT 20", ()),
                ('refundLease', "SELECT id FROM refund_task WHERE status='PROCESSING' AND lease_until<NOW() ORDER BY id LIMIT 20", ())]:
            report['queryPlans'][name] = lab.sql('EXPLAIN ' + query, arguments)
        report['status'] = 'passed'
        versions = lab.sql('SELECT version,COUNT(*) count FROM flyway_schema_history WHERE success=1 GROUP BY version ORDER BY version')
        lab.check('V2 through V6 applied once in isolated schema',
                  all(any(row['version'] == version and row['count'] == 1 for row in versions) for version in ('2', '3', '4', '5', '6')))
        print('Reliability checks complete. Report: ' + str(lab.directory / 'report.json'), flush=True)
    except Exception as error:
        report['status'] = 'failed'
        report['failure'] = type(error).__name__ + ': ' + str(error)
        report['appExitCodesAtFailure'] = {name: proc.poll() for name, proc in lab.apps.items()}
        raise
    finally:
        report['finishedAt'] = datetime.now().astimezone().isoformat()
        try:
            (lab.directory / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding='utf-8')
        finally:
            lab.close()


if __name__ == '__main__':
    main()
