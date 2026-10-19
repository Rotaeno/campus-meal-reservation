"""Same-order concurrent HTTP histories and committed-transition checks.

Triggers exist ONLY in Lab's unique schema and roll back with the business TX.
They are not a production audit feature, and this instrumented run is not a
performance benchmark. No business request is silently retried/repaired.
"""
import argparse
import concurrent.futures as futures
import hashlib
import json
import random
import threading
import time
from datetime import datetime

import requests
from order_state_model import State, Rules, audit_chain, witness
from reliability_lab import Lab, wait


def install_audit(lab):
    lab.sql('''CREATE TABLE test_order_history(
        id BIGINT AUTO_INCREMENT PRIMARY KEY, order_id BIGINT NOT NULL,
        old_status INT, old_paid INT, old_refund VARCHAR(16),
        new_status INT NOT NULL, new_paid INT NOT NULL, new_refund VARCHAR(16) NOT NULL,
        quantity INT NOT NULL, held INT NOT NULL, created_at DATETIME(6) NOT NULL,
        INDEX(order_id,id)) ENGINE=InnoDB''')
    tail = '''NEW.status,NEW.pay_status,NEW.refund_status,NEW.reserved_quantity,
        (SELECT reserved_count FROM meal_session WHERE id=NEW.meal_session_id),NOW(6))'''
    columns = '(order_id,old_status,old_paid,old_refund,new_status,new_paid,new_refund,quantity,held,created_at)'
    insert = ('CREATE TRIGGER test_order_insert AFTER INSERT ON orders FOR EACH ROW '
              'INSERT INTO test_order_history ' + columns + ' VALUES(NEW.id,NULL,NULL,NULL,' + tail)
    update = ('''CREATE TRIGGER test_order_update AFTER UPDATE ON orders FOR EACH ROW BEGIN
        IF NOT(OLD.status <=> NEW.status) OR NOT(OLD.pay_status <=> NEW.pay_status)
          OR NOT(OLD.refund_status <=> NEW.refund_status) THEN
        INSERT INTO test_order_history ''' + columns + ''' VALUES(NEW.id,OLD.status,OLD.pay_status,OLD.refund_status,'''
            + tail + '; END IF; END')
    # MySQL with binary logging requires elevated trigger creation. Restrict the
    # DDL to this generated schema; do not change global trust/binlog settings.
    assert lab.schema.startswith('campus_rel_') and lab.schema[11:].isalnum()
    with lab.db(True) as db, db.cursor() as cursor:
        cursor.execute('USE `' + lab.schema + '`')
        cursor.execute(insert)
        cursor.execute(update)


def state(lab, order):
    row = lab.one('SELECT status,pay_status,refund_status FROM orders WHERE id=%s', (order['id'],))
    return State(row['status'], row['pay_status'], row['refund_status'])


def audit(lab, order):
    rows = lab.sql('SELECT * FROM test_order_history WHERE order_id=%s ORDER BY id', (order['id'],))
    assert audit_chain(rows) == state(lab, order)  # Only called at a quiescent point.
    return rows


def snapshot(lab):
    # One consistent SQL statement. Each test meal has exactly one order.
    rows = lab.sql('''SELECT o.id,o.status,o.pay_status,o.refund_status,o.reserved_quantity,
        m.reserved_count,r.status AS task_status FROM orders o
        JOIN meal_session m ON m.id=o.meal_session_id
        LEFT JOIN refund_task r ON r.order_id=o.id''')
    for row in rows:
        s, p, r, t = row['status'], row['pay_status'], row['refund_status'], row['task_status']
        assert row['reserved_count'] == (0 if s == 6 else row['reserved_quantity']), row
        if s == 6 and p == 1:
            assert (r == 'PENDING' and t in ('PENDING', 'PROCESSING')) or (r == t == 'FAILED'), row
        elif s == 6 and p == 2:
            assert r == t == 'SUCCEEDED', row
        else:
            assert (p == 0 and s in (1, 6) or p == 1 and s in (2, 3, 4, 5)) and r == 'NONE' and t is None, row
    return rows


class Observer:
    def __init__(self, lab):
        self.lab, self.stop = lab, threading.Event()
        self.samples, self.errors = 0, []
        self.thread = threading.Thread(target=self.run, daemon=True)

    def run(self):
        try:
            with (self.lab.directory / 'committed-snapshots.jsonl').open('w', encoding='utf-8') as stream:
                while not self.stop.is_set():
                    rows = snapshot(self.lab)
                    stream.write(json.dumps(dict(atNs=time.perf_counter_ns(), orders=rows)) + '\n')
                    self.samples += 1
                    self.stop.wait(.03)
        except Exception as error:
            self.errors.append(repr(error))

    def start(self):
        self.thread.start()

    def close(self):
        self.stop.set()
        self.thread.join(10)
        assert not self.thread.is_alive() and not self.errors, self.errors


def prepare(lab, target='fresh'):
    actor, meal = lab.actor(), lab.meal()
    lab.cart(actor)
    payload = lab.payload(meal)
    order = lab.submit(actor, payload)
    if target in ('paid', 'preparing', 'ready', 'completed', 'pickup_paid'):
        lab.pay(actor, order)
    if target in ('preparing', 'ready', 'completed'):
        lab.call('a', 'PUT', '/admin/order/confirm', {'id': order['id']}, admin=True)
    if target in ('ready', 'completed'):
        lab.call('a', 'PUT', '/admin/meal/orders/' + str(order['id']) + '/ready', admin=True)
    if target in ('ready', 'completed', 'pickup_paid'):
        # Explicit time fixture, not evidence of naturally elapsed cutoff/pickup.
        lab.sql('''UPDATE meal_session SET meal_date=CURDATE(),cutoff_time=DATE_SUB(NOW(),INTERVAL 2 MINUTE),
            pickup_start=DATE_SUB(NOW(),INTERVAL 1 MINUTE),pickup_end=DATE_ADD(NOW(),INTERVAL 1 HOUR)
            WHERE id=%s''', (meal['id'],))
    if target == 'completed':
        lab.call('a', 'POST', '/admin/meal/redeem', {'pickupCode': order['pickupCode']}, admin=True)
    if target == 'aged':
        lab.sql('UPDATE orders SET order_time=DATE_SUB(NOW(),INTERVAL 16 MINUTE) WHERE id=%s', (order['id'],))
    if target == 'cancelled':
        lab.cancel(actor, order)
    return actor, meal, order, payload


def invoke(lab, job, operation, name):
    actor, _, order, payload = job
    oid = str(order['id'])
    if operation == 'pay':
        return lab.call(name, 'POST', '/user/local/orders/' + order['orderNumber'] + '/callback',
                        {'paidAmount': order['orderAmount']}, actor=actor, expected=None)
    if operation == 'user_cancel':
        return lab.call(name, 'PUT', '/user/order/cancel/' + oid, actor=actor, expected=None)
    if operation == 'admin_cancel':
        return lab.call(name, 'PUT', '/admin/order/cancel', {'id': order['id']}, admin=True, expected=None)
    if operation == 'expire':
        return lab.call(name, 'POST', '/admin/local/timeouts/' + oid, admin=True, expected=None)
    if operation == 'confirm':
        return lab.call(name, 'PUT', '/admin/order/confirm', {'id': order['id']}, admin=True, expected=None)
    if operation == 'ready':
        return lab.call(name, 'PUT', '/admin/meal/orders/' + oid + '/ready', admin=True, expected=None)
    if operation == 'redeem':
        return lab.call(name, 'POST', '/admin/meal/redeem', {'pickupCode': order['pickupCode']}, admin=True, expected=None)
    return lab.call(name, 'POST', '/user/order/submit', payload, actor=actor, expected=None)


SCENARIOS = [
    ('fresh', ['pay', 'pay', 'user_cancel', 'confirm', 'expire', 'replay']),
    ('paid', ['confirm', 'ready', 'user_cancel', 'admin_cancel', 'replay']),
    ('preparing', ['ready', 'user_cancel', 'admin_cancel', 'confirm']),
    ('ready', ['redeem', 'redeem', 'admin_cancel', 'pay', 'user_cancel']),
    ('aged', ['pay', 'pay', 'expire', 'user_cancel', 'replay']),
    ('completed', ['admin_cancel', 'redeem', 'ready', 'pay', 'replay']),
    ('cancelled', ['pay', 'pay', 'user_cancel', 'admin_cancel', 'replay'])]


def overlap(lab, job, plan):
    order = job[2]
    barrier = threading.Barrier(len(plan) + 1)
    def call(index, operation):
        barrier.wait(timeout=5)
        started = time.perf_counter_ns()
        name = 'a' if index % 2 else 'b'
        try:
            response = invoke(lab, job, operation, name)
            ended = time.perf_counter_ns()
            assert response['_http'] == 200 and response.get('code') in (0, 1), response
            kind = 'reject' if response['code'] == 0 else (
                str(response['data']).lower() if operation == 'expire' else 'ok')
            if operation == 'replay' and kind == 'ok':
                assert response['data']['id'] == order['id']
            return dict(operation=operation, instance=name, startNs=started, endNs=ended, result=kind)
        finally:
            lab.release_thread_session()
    with lab.db() as blocker, blocker.cursor() as cursor, futures.ThreadPoolExecutor(len(plan)) as pool:
        blocker.begin()
        cursor.execute('SELECT id FROM orders WHERE id=%s FOR UPDATE', (order['id'],))
        pending = [pool.submit(call, i, operation) for i, operation in enumerate(plan)]
        barrier.wait(timeout=5)
        observations, failure = [], None
        try:
            def waiting():
                count = lab.scalar('''SELECT COUNT(DISTINCT w.REQUESTING_ENGINE_TRANSACTION_ID)
                    FROM performance_schema.data_lock_waits w JOIN performance_schema.data_locks l
                    ON w.REQUESTING_ENGINE_LOCK_ID=l.ENGINE_LOCK_ID AND w.ENGINE=l.ENGINE
                    WHERE l.OBJECT_SCHEMA=%s AND l.OBJECT_NAME='orders' ''', (lab.schema,), root=True)
                # A CAS with a mismatching state can legitimately reject before
                # requesting the row lock. Do not require it to block artificially.
                rejected = [index for index, future in enumerate(pending) if future.done()
                            and plan[index] in ('confirm', 'ready') and future.result()['result'] == 'reject']
                observations.append(dict(waiters=count, earlyRejected=rejected))
                return count + len(rejected) >= len(plan)
            blocked = wait(waiting, timeout=3, description='conflicting requests waiting or valid early CAS rejections')
        except Exception as error:
            failure = error
        finally:
            blocker.rollback()
        history = [future.result(timeout=25) for future in pending]
    if failure:
        (lab.directory / ('overlap-failure-' + str(order['id']) + '.json')).write_text(
            json.dumps(dict(plan=plan, observations=observations, history=history), indent=2), encoding='utf-8')
        raise failure
    return history, dict(confirmed=bool(blocked), observations=observations)


def concurrent_case(lab, target, operations, seed):
    job = prepare(lab, target)
    order = job[2]
    initial = state(lab, order)
    rules = Rules(expired=target in ('aged', 'ready', 'completed'),
                  cutoff=target in ('ready', 'completed'), pickup=target in ('ready', 'completed'))
    plan = list(operations)
    random.Random(seed).shuffle(plan)
    history, blocked = overlap(lab, job, plan)
    final = state(lab, order)
    ordering = witness(initial, final, history, rules)
    rows = audit(lab, order)
    evidence = dict(target=target, seed=seed, initial=initial.__dict__, final=final.__dict__,
                    rules=rules.__dict__, history=history, witness=ordering, committedChanges=len(rows),
                    databaseOverlapConfirmed=bool(blocked), compensatingRequests=0)
    evidence['databaseOverlapObservation'] = blocked
    (lab.directory / ('history-' + target + '-' + str(seed) + '.json')).write_text(
        json.dumps(dict(evidence, committedHistory=rows), indent=2, default=str), encoding='utf-8')
    lab.check('same-order ' + target + ' seed ' + str(seed) + ': legal real-time HTTP history and committed changes',
              ordering is not None, evidence)
    return job, evidence


def staged_case(lab, seed):
    job = prepare(lab, 'pickup_paid')
    order, initial = job[2], state(lab, job[2])
    rules = Rules(expired=True, cutoff=True, pickup=True)
    history, overlap_observations = [], []
    # Seven operations, four waves. Earlier responses precede later invocations.
    # Two redemptions in the last wave must produce exactly one final success
    # across both redemption waves, regardless of the middle wave's ordering.
    for wave in (['ready', 'redeem'], ['confirm'], ['ready', 'redeem'], ['redeem', 'redeem']):
        plan = list(wave)
        random.Random(seed + len(history)).shuffle(plan)
        calls, observation = overlap(lab, job, plan)
        history.extend(calls)
        overlap_observations.append(observation)
    final = state(lab, order)
    ordering = witness(initial, final, history, rules)
    rows = audit(lab, order)
    precedence = sum(a['endNs'] < b['startNs'] for a in history for b in history)
    lab.check('staged fulfillment seed ' + str(seed) + ': actual precedence and exactly one redemption',
              ordering is not None and final == State(5, 1) and precedence > 0
              and sum(item['operation'] == 'redeem' and item['result'] == 'ok' for item in history) == 1)
    evidence = dict(target='staged_fulfillment', seed=seed, history=history, witness=ordering,
                    initial=initial.__dict__, final=final.__dict__, precedenceEdges=precedence,
                    committedChanges=len(rows), compensatingRequests=0, databaseOverlapConfirmed=True)
    evidence['databaseOverlapObservation'] = overlap_observations
    (lab.directory / ('history-staged-' + str(seed) + '.json')).write_text(
        json.dumps(dict(evidence, committedHistory=rows), indent=2, default=str), encoding='utf-8')
    return job, evidence


def scheduled_flags():
    return ['--sky.refund.scan-enabled=true', '--sky.refund.interval-ms=200', '--sky.refund.lease-seconds=2']


def automatic_recovery(lab, jobs):
    before = {job[2]['id']: state(lab, job[2]) for job in jobs}
    for name in ('a', 'b'):
        lab.app_overrides[name] = scheduled_flags()
        lab.restart_app(name)
    pending = [job for job in jobs if before[job[2]['id']] == State(6, 1, 'PENDING')]
    wait(lambda: all(state(lab, job[2]) == State(6, 2, 'SUCCEEDED') for job in pending),
         timeout=20, description='automatic refund after concurrent phase; no compensating business calls')
    for job in jobs:
        order = job[2]
        observed = state(lab, order)
        expected = State(6, 2, 'SUCCEEDED') if job in pending else before[order['id']]
        assert observed == expected
        audit(lab, order)
        ledger = lab.ledger(order)
        assert ledger['actualPayments'] == int(observed.paid > 0), ledger
        assert ledger['actualRefunds'] == int(observed.paid == 2), ledger
    lab.check('automatic phase changes only pending refunds; independent ledger agrees without final pay/cancel calls',
              True, dict(orders=len(jobs), refunded=len(pending), compensatingRequests=0))
    return dict(orders=len(jobs), refunded=len(pending), compensatingRequests=0)


def crash_audit(lab):
    # Only A scans; B cannot legitimately reclaim until A has been killed.
    lab.app_overrides['b'] = ['--sky.refund.scan-enabled=false']
    lab.restart_app('b')
    job = prepare(lab, 'paid')
    actor, _, order, _ = job
    gate = lab.arm('a', 'refund.beforeCommit', 'R' + order['orderNumber'])
    lab.cancel(actor, order)
    wait(lambda: lab.reached('a', gate), description='refund before local commit')
    rows = audit(lab, order)
    assert state(lab, order) == State(6, 1, 'PENDING') and lab.ledger(order)['actualRefunds'] == 1
    before = lab.task(order)
    lab.stop(lab.apps['a'])
    assert audit(lab, order) == rows, 'Uncommitted audit write survived process crash'
    lab.app_overrides['b'] = scheduled_flags()
    lab.restart_app('b')
    wait(lambda: state(lab, order) == State(6, 2, 'SUCCEEDED'), timeout=15, description='autonomous replacement executor')
    after = audit(lab, order)
    ledger = lab.ledger(order)
    lab.check('real refund crash rolls back confirmation and audit together; replacement adds one committed success',
              len(after) == len(rows) + 1 and ledger['actualRefunds'] == 1
              and lab.task(order)['lease_token'] != before['lease_token'],
              dict(beforeCommittedChanges=len(rows), afterCommittedChanges=len(after), **ledger, compensatingRequests=0))
    lab.start_app('a')


def uncertain_payment(lab):
    job = prepare(lab)
    order = job[2]
    gate = lab.arm('a', 'payment.providerRecorded', order['orderNumber'])
    with futures.ThreadPoolExecutor(1) as pool:
        future = pool.submit(invoke, lab, job, 'pay', 'a')
        wait(lambda: lab.reached('a', gate), description='payment recorded externally, not applied locally')
        lab.stop(lab.apps['a'])
        try:
            future.result(timeout=5)
            raise AssertionError('Killed payment unexpectedly returned a complete response')
        except requests.RequestException:
            pass
    # Observation only: no callback or order mutation. Payment reconciliation is not implemented.
    deadline = time.perf_counter() + 2
    samples = 0
    while time.perf_counter() < deadline:
        assert state(lab, order) == State(1, 0)
        samples += 1
        time.sleep(.08)
    assert lab.ledger(order)['actualPayments'] == 1
    unassisted = state(lab, order).__dict__
    response = invoke(lab, job, 'pay', 'b')  # Explicitly counted external callback redelivery.
    assert response['code'] == 1 and state(lab, order) == State(2, 1)
    audit(lab, order)
    result = dict(observationSeconds=2, samples=samples, unassistedState=unassisted,
                  automaticPaymentReconciliation=False, explicitCallbackRedeliveries=1,
                  finalState=state(lab, order).__dict__, **lab.ledger(order))
    lab.check('lost payment callback is not called automatic recovery; one explicit redelivery applies it', True, result)
    lab.start_app('a')
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--seeds', default='20261019,20261020,20261021')
    args = parser.parse_args()
    seeds = [int(value) for value in args.seeds.split(',')]
    assert 1 <= len(seeds) <= 10
    lab = Lab()
    # Orchestration budget must be lower than DB lock timeout, with enough room
    # for local HTTP/provider dispatch. This is not a production latency target.
    lab.lock_wait_seconds = 5
    report = dict(date=datetime.now().astimezone().isoformat(), status='running', checks=lab.checks,
                  jarSha256=hashlib.sha256(lab.jar.read_bytes()).hexdigest(), seeds=seeds, histories=[])
    observer = None
    try:
        lab.setup()
        install_audit(lab)
        report['environment'] = dict(lab.environment_info(), instrumentation='isolated InnoDB audit triggers',
                                     timeoutAutoScan=False, synchronousPhaseRefundAutoScan=False,
                                     recoveryPhaseRefundAutoScan=True, recoveryRefundScanMs=200)
        observer = Observer(lab)
        observer.start()
        jobs = []
        for seed in seeds:
            for target, operations in SCENARIOS:
                job, evidence = concurrent_case(lab, target, operations, seed)
                jobs.append(job)
                report['histories'].append(evidence)
            job, evidence = staged_case(lab, seed)
            jobs.append(job)
            report['histories'].append(evidence)
        report['automaticRecovery'] = automatic_recovery(lab, jobs)
        crash_audit(lab)
        report['uncertainPayment'] = uncertain_payment(lab)
        lab.invariants()
        observer.close()
        report['committedSnapshotSamples'] = observer.samples
        report['committedAuditChanges'] = lab.scalar('SELECT COUNT(*) FROM test_order_history')
        report['status'] = 'passed'
    except Exception as error:
        report['failure'] = type(error).__name__ + ': ' + str(error)
        report['status'] = 'failed'
        raise
    finally:
        if observer:
            observer.stop.set()
            observer.thread.join(10)
            report['observerErrors'] = observer.errors
        report['finishedAt'] = datetime.now().astimezone().isoformat()
        (lab.directory / 'state-history-report.json').write_text(json.dumps(report, indent=2, default=str), encoding='utf-8')
        print('State history report: ' + str(lab.directory / 'state-history-report.json'), flush=True)
        lab.close()


if __name__ == '__main__':
    main()
