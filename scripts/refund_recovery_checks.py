"""Refund review and queue progress in an owned two-instance lab."""
import argparse
import concurrent.futures as futures
import hashlib
import json
import time
from datetime import datetime
from pathlib import Path
import requests

from reliability_lab import Lab, wait


def flags(review_seconds=86400):
    return ['--sky.refund.scan-enabled=true', '--sky.refund.interval-ms=200', '--sky.refund.lease-seconds=20',
            '--sky.refund.poll-seconds=1', '--sky.refund.review-after-seconds=' + str(review_seconds)]


def paid_job(lab):
    actor, meal = lab.actor(), lab.meal()
    lab.cart(actor)
    order = lab.submit(actor, lab.payload(meal))
    lab.pay(actor, order)
    return actor, meal, order


def manual_review(lab):
    # Force one actual refund, then six unconfirmed local outcomes. Real waits,
    # not SQL edits to failure counters or next_retry_time.
    actor, _, order = paid_job(lab)
    lab.fault(order, 0, 'INVALID_JSON')
    lab.fault(order, 0, kind='query', httpStatus=503, repeat=5)
    lab.cancel(actor, order)
    wait(lambda: lab.task(order)['status'] == 'FAILED', timeout=110, description='six automatic failures and natural retry delays')
    task = lab.task(order)
    ledger = lab.ledger(order)
    lab.check('six actual unconfirmed outcomes stop automatically without declaring funds unpaid',
              task['failures'] == 6 and task['provider_state'] == 'UNKNOWN'
              and ledger['actualRefunds'] == ledger['refundCalls'] == 1 and ledger['queryCalls'] == 5,
              dict(ledger, localState=task['status'], providerState=task['provider_state'], failures=task['failures']))
    for name in ('a', 'b'):
        lab.app_overrides[name] = ['--sky.refund.scan-enabled=false']
        lab.restart_app(name)
    lab.stop(lab.merchant)
    lab.start_provider()  # Persisted fault counters, payment and refund survive restart.
    assert lab.task(order)['failures'] == 6 and lab.ledger(order)['actualRefunds'] == 1
    tid = str(task['id'])
    bad = lab.call('a', 'POST', '/admin/refunds/' + tid + '/retry', {'reason': '   '}, admin=True, expected=None)
    unauthorized = requests.post(lab.base('a') + '/admin/refunds/' + tid + '/retry', json={'reason': 'untrusted'}, timeout=5)
    lab.check('manual recovery requires admin authentication and a non-empty reason',
              bad['_http'] == 200 and bad['code'] == 0 and unauthorized.status_code == 401
              and lab.task(order)['status'] == 'FAILED')
    # A real SQL failure AFTER changing the task must roll back both the retry
    # and its audit. This is a transaction test, not a process-crash claim.
    assert lab.schema.startswith('campus_rel_') and lab.schema[11:].isalnum()
    with lab.db(True) as db, db.cursor() as cursor:
        cursor.execute('USE `' + lab.schema + '`')
        cursor.execute("CREATE TRIGGER test_review_guard BEFORE INSERT ON refund_review FOR EACH ROW SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='test review rollback'")
    try:
        failed = lab.call('a', 'POST', '/admin/refunds/' + tid + '/retry', {'reason': '故意使记录写入失败'}, admin=True, expected=None)
        lab.check('review insert failure rolls back prior task reset and leaves no review row',
                  failed['_http'] == 503 and lab.task(order)['status'] == 'FAILED'
                  and lab.task(order)['failures'] == 6
                  and lab.scalar('SELECT COUNT(*) FROM refund_review WHERE task_id=%s', (task['id'],)) == 0)
    finally:
        with lab.db(True) as db, db.cursor() as cursor:
            cursor.execute('DROP TRIGGER `' + lab.schema + '`.test_review_guard')
    def retry(name):
        try:
            return lab.call(name, 'POST', '/admin/refunds/' + tid + '/retry', {'reason': '已核查平台结果，查询原退款号'}, admin=True, expected=None)
        finally:
            lab.release_thread_session()
    with futures.ThreadPoolExecutor(2) as pool:
        responses = list(pool.map(retry, ['a', 'b']))
    reviews = lab.call('a', 'GET', '/admin/refunds/' + tid + '/reviews', admin=True)['data']
    lab.check('two-instance manual recovery records one authenticated decision and preserves the original refund',
              sum(x['code'] == 1 for x in responses) == sum(x['code'] == 0 for x in responses) == 1
              and len(reviews) == 1 and reviews[0]['operatorId'] == 1
              and reviews[0]['previousProviderState'] == 'UNKNOWN' and reviews[0]['previousFailures'] == 6
              and lab.task(order)['refund_number'] == task['refund_number']
              and lab.task(order)['create_time'] == task['create_time'])
    for name in ('a', 'b'):
        lab.app_overrides[name] = flags()
        lab.restart_app(name)
    wait(lambda: lab.task(order)['status'] == 'SUCCEEDED', timeout=10, description='reviewed refund confirms through query')
    ledger = lab.ledger(order)
    lab.check('reviewed recovery queries existing refund; no second refund POST after app and provider restart',
              ledger['actualRefunds'] == ledger['refundCalls'] == 1 and ledger['queryCalls'] == 6
              and lab.task(order)['provider_state'] == 'SUCCEEDED', ledger)
    return dict(**ledger, failuresBeforeReview=6, reviews=len(reviews), realBackoff=True)


def processing_and_terminal(lab):
    for name in ('a', 'b'):
        lab.app_overrides[name] = flags(3)
        lab.restart_app(name)
    actor, _, order = paid_job(lab)
    lab.fault(order, 0, 'PROCESSING')
    lab.fault(order, 0, 'PROCESSING', kind='query', repeat=20)
    lab.cancel(actor, order)
    wait(lambda: lab.task(order)['status'] == 'FAILED', timeout=10, description='processing exceeds configured review age')
    task = lab.task(order)
    lab.check('prolonged PROCESSING stops with last provider result and review reason, not a funds-failure assertion',
              task['provider_state'] == 'PROCESSING' and task['last_error'] == 'PROCESSING_TOO_LONG'
              and task['failures'] == 0 and task['provider_submitted'] == 1,
              dict(attempts=task['attempts'], failures=task['failures'], providerState=task['provider_state']))
    lab.fault(order, 0, 'SUCCESS', kind='query')
    lab.call('a', 'POST', '/admin/refunds/' + str(task['id']) + '/retry', {'reason': '平台处理结束，复查原退款'}, admin=True)
    wait(lambda: lab.task(order)['status'] == 'SUCCEEDED', timeout=8, description='aged reviewed task may still confirm success')
    lab.check('manual confirmation of aged processing preserves refund number and never resubmits',
              lab.ledger(order)['refundCalls'] == lab.ledger(order)['actualRefunds'] == 1)
    for status in ('CLOSED', 'ABNORMAL'):
        actor, _, order = paid_job(lab)
        lab.fault(order, 0, status, recordRefund=False)
        lab.cancel(actor, order)
        wait(lambda: lab.task(order)['status'] == 'FAILED', timeout=5, description='explicit external terminal result')
        task = lab.task(order)
        lab.check('external ' + status + ' remains distinct from an unknown transport outcome',
                  task['provider_state'] == status and task['last_error'] == 'PROVIDER_' + status
                  and lab.ledger(order)['actualRefunds'] == 0
                  and lab.scalar('SELECT pay_status FROM orders WHERE id=%s', (order['id'],)) == 1)


def unknown_before_commit(lab):
    for name in ('a', 'b'):
        lab.app_overrides[name] = ['--sky.refund.scan-enabled=false']
        lab.restart_app(name)
    actor, _, order, task = lab.refunded_order()
    lab.fault(order, 0, httpStatus=503, recordRefund=False)
    lab.run_refund(task)
    lab.check('provider rejected before refund ledger commit leaves zero actual refunds',
              lab.ledger(order)['actualRefunds'] == 0 and lab.task(order)['failures'] == 1)
    wait(lambda: lab.due(order), description='natural refund retry backoff')
    lab.run_refund(task, 'b')
    ledger = lab.ledger(order)
    lab.check('explicit NOT_FOUND permits same-number submission after query, then one actual refund',
              lab.task(order)['status'] == 'SUCCEEDED' and ledger['refundCalls'] == 2
              and ledger['queryCalls'] == ledger['actualRefunds'] == 1, ledger)
    for name in ('a', 'b'):
        lab.app_overrides[name] = flags()
        lab.restart_app(name)
    return ledger


def locked_page(lab, baseline):
    jobs = [lab.refunded_order() for _ in range(21)]
    blocked_ids = [job[3]['id'] for job in jobs[:20]]
    healthy = jobs[-1][2]
    diagnostic = {}
    if not baseline:
        original = 'SELECT id FROM refund_task WHERE id IN (' + ','.join(['%s'] * 20) + ') FOR UPDATE'
        diagnostic['originalInPlan'] = lab.sql('EXPLAIN ' + original, tuple(blocked_ids))
        with lab.db() as original_blocker, original_blocker.cursor() as cursor:
            original_blocker.begin()
            cursor.execute(original, tuple(blocked_ids))
            with lab.db() as check_db, check_db.cursor() as check:
                check_db.begin()
                try:
                    check.execute('SELECT id FROM refund_task WHERE id=%s FOR UPDATE NOWAIT', (jobs[-1][3]['id'],))
                    diagnostic['originalInAlsoLockedHealthy'] = False
                except Exception as error:
                    assert error.args[0] == 3572, error
                    diagnostic['originalInAlsoLockedHealthy'] = True
                finally:
                    check_db.rollback()
            original_blocker.rollback()
    with lab.db() as blocker, blocker.cursor() as cursor:
        blocker.begin()
        # On a 21-row table, IN(20 ids) can choose a full scan under RR and
        # retain locks on non-matching rows. Use exact primary-key lookups.
        for identifier in blocked_ids:
            cursor.execute('SELECT id FROM refund_task WHERE id=%s FOR UPDATE', (identifier,))
        with lab.db() as observer, observer.cursor() as check:
            observer.begin()
            check.execute('SELECT id FROM refund_task WHERE id=%s FOR UPDATE NOWAIT', (jobs[-1][3]['id'],))
            assert check.fetchone()[0] == jobs[-1][3]['id']
            observer.rollback()
        lab.check('lock fixture leaves the twenty-first row independently lockable before scanning')
        lab.app_overrides['a'] = flags()
        lab.restart_app('a')
        if baseline:
            deadline = time.perf_counter() + 6
            samples = 0
            while time.perf_counter() < deadline:
                assert lab.task(healthy)['status'] == 'PENDING'
                assert lab.ledger(healthy)['actualRefunds'] == 0
                samples += 1
                time.sleep(.1)
            evidence = dict(lockedTasks=20, healthyStatus=lab.task(healthy)['status'], observationSeconds=6, samples=samples)
            lab.check('baseline: a fully locked candidate page prevents later eligible work during observation', True, evidence)
        else:
            wait(lambda: lab.task(healthy)['status'] == 'SUCCEEDED', timeout=8, description='unlocked refund beyond locked page')
            evidence = dict(lockedTasks=20, healthyStatus=lab.task(healthy)['status'], fixtureDiagnostic=diagnostic,
                            lockedAttempts=[lab.task(job[2])['attempts'] for job in jobs[:20]])
            lab.check('unlocked task beyond twenty locked candidates progresses without claiming locked tasks',
                      all(value == 0 for value in evidence['lockedAttempts']), evidence)
        blocker.rollback()
    wait(lambda: all(lab.task(job[2])['status'] == 'SUCCEEDED' for job in jobs), timeout=30, description='released queue drains')
    lab.check('after releasing real locks all twenty-one tasks settle with one actual refund each',
              all(lab.ledger(job[2])['actualRefunds'] == 1 for job in jobs))
    lab.invariants()
    return evidence


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--baseline', action='store_true')
    parser.add_argument('--jar', type=Path)
    parser.add_argument('--fairness-only', action='store_true')
    args = parser.parse_args()
    lab = Lab()
    if args.jar:
        lab.jar = args.jar.resolve()
    report = dict(date=datetime.now().astimezone().isoformat(), status='running', baseline=args.baseline,
                  checks=lab.checks, jarSha256=hashlib.sha256(lab.jar.read_bytes()).hexdigest())
    try:
        lab.setup()
        report['environment'] = lab.environment_info()
        report['lockedPage'] = locked_page(lab, args.baseline)
        if not args.baseline and not args.fairness_only:
            report['notProcessed'] = unknown_before_commit(lab)
            report['manualReview'] = manual_review(lab)
            processing_and_terminal(lab)
            lab.invariants()
            report['environment'].update(refundAutoScan=True, refundScanMs=200, pollSeconds=1,
                                         leaseSeconds=20, reviewAgeVariantsSeconds=[86400, 3])
        report['status'] = 'passed'
    except Exception as error:
        report['status'] = 'failed'
        report['failure'] = type(error).__name__ + ': ' + str(error)
        raise
    finally:
        report['finishedAt'] = datetime.now().astimezone().isoformat()
        (lab.directory / 'refund-recovery-report.json').write_text(json.dumps(report, indent=2, default=str), encoding='utf-8')
        print('Refund recovery report: ' + str(lab.directory / 'refund-recovery-report.json'), flush=True)
        lab.close()


if __name__ == '__main__':
    main()
