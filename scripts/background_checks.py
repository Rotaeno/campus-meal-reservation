"""Actual scheduled recovery experiments in an owned, isolated two-app lab.
The --baseline mode records the pre-fix single-scheduler blocking behavior.
"""
import argparse
import hashlib
import json
import time
from datetime import datetime, timedelta
from pathlib import Path

from reliability_lab import Lab, wait


def scheduled_options():
    return ['--sky.refund.scan-enabled=true', '--sky.refund.interval-ms=200',
            '--sky.order.timeout-scan-ms=200', '--sky.refund.poll-seconds=30', '--sky.refund.lease-seconds=20']


def prepare(lab):
    actor, meal = lab.actor(), lab.meal()
    lab.cart(actor)
    order = lab.submit(actor, lab.payload(meal))
    return actor, meal, order


def interference(lab, baseline=False):
    slow_actor, slow_meal, slow = prepare(lab)
    quick_actor, quick_meal, quick = prepare(lab)
    unpaid_actor, unpaid_meal, unpaid = prepare(lab)
    lab.pay(slow_actor, slow)
    lab.pay(quick_actor, quick)
    lab.fault(slow, 5000)
    lab.cancel(slow_actor, slow)
    wait(lambda: lab.ledger(slow)['actualRefunds'] == 1, description='slow provider committed and is delaying response')
    # A disclosed expiry fixture avoids waiting 15 minutes. It is not a crash/recovery claim.
    lab.sql('UPDATE orders SET order_time=DATE_SUB(NOW(),INTERVAL 16 MINUTE) WHERE id=%s', (unpaid['id'],))
    started = time.perf_counter()
    lab.cancel(quick_actor, quick)
    times = {}
    def done():
        if 'unpaidCancelledMs' not in times and lab.scalar('SELECT status FROM orders WHERE id=%s', (unpaid['id'],)) == 6:
            times['unpaidCancelledMs'] = round((time.perf_counter() - started) * 1000, 2)
        if 'quickRefundMs' not in times and lab.task(quick)['status'] == 'SUCCEEDED':
            times['quickRefundMs'] = round((time.perf_counter() - started) * 1000, 2)
        return len(times) == 2
    wait(done, timeout=15, description='automatic timeout and quick refund')
    wait(lambda: lab.task(slow)['status'] == 'SUCCEEDED', timeout=10, description='slow refund finishes')
    lab.invariants()
    lab.check('scheduled slow refund interference ' + ('before fix' if baseline else 'after fix'),
              (min(times.values()) > 3000 if baseline else max(times.values()) < 3000), times)
    lab.check('automatic expiry releases capacity; independent ledgers agree',
              lab.scalar('SELECT reserved_count FROM meal_session WHERE id=%s', (unpaid_meal['id'],)) == 0
              and lab.ledger(quick)['actualRefunds'] == lab.ledger(slow)['actualRefunds'] == 1)
    return times


def saturation(lab):
    lab.app_overrides['a'] = scheduled_options() + ['--sky.refund.workers=1', '--sky.refund.queue-capacity=1']
    lab.restart_app('a')
    jobs = [prepare(lab) for _ in range(6)]
    for actor, _, order in jobs:
        lab.pay(actor, order)
    lab.fault(jobs[0][2], 5000)
    lab.cancel(jobs[0][0], jobs[0][2])
    wait(lambda: lab.ledger(jobs[0][2])['actualRefunds'] == 1, description='one worker occupied')
    for actor, _, order in jobs[1:]:
        lab.cancel(actor, order)
    wait(lambda: lab.metrics('a')['refundExecutor']['rejected'] > 0, timeout=3, description='bounded queue rejects admission')
    snapshot = lab.metrics('a')['refundExecutor']
    unclaimed = [lab.task(order) for _, _, order in jobs[1:]]
    lab.check('full queue leaves tasks unclaimed and does not run on scheduler',
              snapshot['active'] == snapshot['queued'] == 1 and snapshot['admitted'] <= 2
              and all(row['status'] == 'PENDING' and row['attempts'] == 0 for row in unclaimed), snapshot)
    wait(lambda: all(lab.task(order)['status'] == 'SUCCEEDED' for _, _, order in jobs), timeout=20,
         description='rejected queued work recovered automatically')
    lab.check('all six refunds drain after queue saturation with one ledger refund each',
              all(lab.ledger(order)['actualRefunds'] == 1 for _, _, order in jobs))
    lab.invariants()
    return snapshot


def automatic_crash(lab):
    lab.app_overrides['a'] = scheduled_options()[:-1] + ['--sky.refund.lease-seconds=2']
    lab.restart_app('a')
    actor, _, order = prepare(lab)
    lab.pay(actor, order)
    gate = lab.arm('a', 'refund.beforeCommit', 'R' + order['orderNumber'])
    lab.cancel(actor, order)
    wait(lambda: lab.reached('a', gate), description='automatic refund at local beforeCommit')
    before = lab.task(order)
    lab.check('automatic worker has external commit but local confirmation is uncommitted',
              before['status'] == 'PROCESSING' and lab.ledger(order)['actualRefunds'] == 1
              and lab.scalar('SELECT pay_status FROM orders WHERE id=%s', (order['id'],)) == 1)
    started = time.perf_counter()
    lab.stop(lab.apps['a'])
    lab.stop(lab.merchant)
    lab.start_provider()  # Same independent SQLite file; no local confirmation manufactured.
    lab.app_overrides['b'] = list(lab.app_overrides['a'])
    lab.restart_app('b')
    wait(lambda: lab.task(order)['status'] == 'SUCCEEDED', timeout=15, description='second instance autonomous lease recovery')
    after = lab.task(order)
    evidence = dict(lab.ledger(order), attempts=after['attempts'],
                    recoveryIncludingStartupMs=round((time.perf_counter() - started) * 1000, 2))
    lab.check('restart and actual lease expiry recover without manual refund endpoint',
              after['lease_token'] != before['lease_token'] and after['attempts'] == 2
              and evidence['refundCalls'] == evidence['queryCalls'] == 1 and evidence['actualRefunds'] == 1, evidence)
    lab.start_app('a')
    lab.invariants()
    return evidence


def claim_failure_isolation(lab):
    lab.app_overrides['a'] = ['--sky.refund.scan-enabled=false']
    lab.restart_app('a')
    jobs = [prepare(lab) for _ in range(2)]
    for actor, _, order in jobs:
        lab.pay(actor, order)
        lab.cancel(actor, order)
    bad = lab.task(jobs[0][2])
    lab.app_overrides['a'] = scheduled_options()
    with lab.db() as blocker, blocker.cursor() as cursor:
        blocker.begin()
        cursor.execute('UPDATE refund_task SET id=id WHERE id=%s', (bad['id'],))
        lab.restart_app('a')
        wait(lambda: lab.task(jobs[1][2])['status'] == 'SUCCEEDED', timeout=8,
             description='healthy task proceeds while another claim is locked')
        snapshot = lab.metrics('a')
        lab.check('candidate scan skips a real locked task while healthy background work proceeds',
                  lab.task(jobs[0][2])['status'] == 'PENDING' and lab.task(jobs[0][2])['attempts'] == 0,
                  {'dbRetries': snapshot['dbRetries'], 'workerFailures': snapshot['refundExecutor']['failures']})
        blocker.rollback()
    wait(lambda: lab.task(jobs[0][2])['status'] == 'SUCCEEDED', timeout=10, description='later scan recovers released claim')
    lab.check('released candidate is recovered by a later automatic scan', lab.ledger(jobs[0][2])['actualRefunds'] == 1)


def natural_expiry(lab):
    actor = lab.actor()
    now = datetime.now()
    cutoff = now + timedelta(seconds=4)
    start, end = now + timedelta(minutes=1), now + timedelta(minutes=10)
    if start.date() != end.date():
        start = datetime.combine(end.date(), datetime.min.time()) + timedelta(minutes=1)
        end = start + timedelta(minutes=9)
    meal = lab.call('a', 'POST', '/admin/meal/sessions',
                    {'title': 'natural-expiry', 'mealDate': str(start.date()), 'capacity': 2,
                     'cutoffTime': cutoff.strftime('%Y-%m-%d %H:%M:%S'),
                     'pickupStart': start.strftime('%Y-%m-%d %H:%M:%S'),
                     'pickupEnd': end.strftime('%Y-%m-%d %H:%M:%S')}, admin=True)['data']
    lab.cart(actor)
    order = lab.submit(actor, lab.payload(meal))
    wait(lambda: lab.scalar('SELECT status FROM orders WHERE id=%s', (order['id'],)) == 6, timeout=10,
         description='natural cutoff automatically expires unpaid reservation')
    lab.check('natural cutoff expires fresh order and releases capacity without SQL time edits',
              lab.scalar('SELECT reserved_count FROM meal_session WHERE id=%s', (meal['id'],)) == 0
              and lab.scalar('SELECT COUNT(*) FROM refund_task WHERE order_id=%s', (order['id'],)) == 0)


def processing_poll(lab):
    actor, _, order = prepare(lab)
    lab.pay(actor, order)
    lab.fault(order, 0, 'PROCESSING')
    lab.cancel(actor, order)
    wait(lambda: lab.task(order)['provider_submitted'] == 1, description='background persists PROCESSING submission')
    wait(lambda: lab.task(order)['status'] == 'SUCCEEDED', timeout=40, description='automatic 30-second provider query')
    ledger = lab.ledger(order)
    lab.check('automatic PROCESSING recovery queries instead of another refund POST',
              ledger['refundCalls'] == ledger['queryCalls'] == ledger['actualRefunds'] == 1, ledger)
    lab.invariants()


def backlog(lab):
    # Both instances are now scanning; persistent admission is still the task-row CAS.
    jobs = [prepare(lab) for _ in range(40)]
    for actor, _, order in jobs:
        lab.pay(actor, order)
    started = time.perf_counter()
    for actor, _, order in jobs:
        lab.cancel(actor, order)
    wait(lambda: all(lab.task(order)['status'] == 'SUCCEEDED' for _, _, order in jobs), timeout=30,
         description='both automatic scanners drain forty real paid refund intents')
    evidence = {'orders': len(jobs), 'drainIncludingCancellationMs': round((time.perf_counter() - started) * 1000, 2)}
    lab.check('two automatic instances drain real refund backlog with one ledger refund per order',
              all(lab.ledger(order)['actualRefunds'] == 1 for _, _, order in jobs), evidence)
    lab.invariants()
    return evidence


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--baseline', action='store_true')
    parser.add_argument('--jar', type=Path)
    args = parser.parse_args()
    lab = Lab()
    if args.jar:
        lab.jar = args.jar.resolve()
    lab.app_overrides['a'] = scheduled_options()
    report = {'date': datetime.now().astimezone().isoformat(), 'status': 'running',
              'baseline': args.baseline, 'checks': lab.checks,
              'jarSha256': hashlib.sha256(lab.jar.read_bytes()).hexdigest()}
    try:
        lab.setup()
        report['environment'] = dict(lab.environment_info(), refundAutoScan=True,
                                      leaseSeconds=20, timeoutScanMs=200, refundScanMs=200, initialScheduledInstances=1,
                                      initialLeaseSeconds=20, leaseVariantsSeconds=[20, 2],
                                      providerPollSeconds=30,
                                      workerVariants=[2, 1], queueCapacityVariants=[16, 1])
        report['interference'] = interference(lab, args.baseline)
        if not args.baseline:
            report['saturation'] = saturation(lab)
            claim_failure_isolation(lab)
            report['automaticCrash'] = automatic_crash(lab)
            natural_expiry(lab)
            processing_poll(lab)
            report['backlog'] = backlog(lab)
        report['status'] = 'passed'
    except Exception as error:
        report['status'] = 'failed'
        report['failure'] = type(error).__name__ + ': ' + str(error)
        raise
    finally:
        report['finishedAt'] = datetime.now().astimezone().isoformat()
        (lab.directory / 'background-report.json').write_text(json.dumps(report, indent=2, default=str), encoding='utf-8')
        print('Background report: ' + str(lab.directory / 'background-report.json'), flush=True)
        lab.close()


if __name__ == '__main__':
    main()
