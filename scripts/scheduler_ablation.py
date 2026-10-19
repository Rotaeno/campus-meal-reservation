"""Same-artifact 2x2 scheduler experiment, including saturated refund workers.

Inline execution is explicitly gated by local + reliability enabled. B is idle
for this experiment; all business data/merchant records remain in owned labs.
"""
import argparse
import hashlib
import json
import random
import statistics
import time
from datetime import datetime

from background_checks import prepare
from reliability_lab import Lab, wait


VARIANTS = [('inline', 1), ('inline', 2), ('async', 1), ('async', 2)]


def options(mode, schedulers, workers, capacity):
    return ['--sky.refund.scan-enabled=true', '--sky.refund.interval-ms=200',
            '--sky.order.timeout-scan-ms=200', '--sky.refund.lease-seconds=30',
            '--spring.task.scheduling.pool.size=' + str(schedulers),
            '--sky.refund.workers=' + str(workers), '--sky.refund.queue-capacity=' + str(capacity),
            '--sky.local.reliability.synchronous-refund-scan=' + str(mode == 'inline').lower()]


def run_case(lab, mode, schedulers, slow_count, args, round_index):
    lab.app_overrides['a'] = ['--sky.refund.scan-enabled=false']
    lab.restart_app('a')
    slow = [prepare(lab) for _ in range(slow_count)]
    quick, unpaid = prepare(lab), prepare(lab)
    for actor, _, order in slow + [quick]:
        lab.pay(actor, order)
    for actor, _, order in slow:
        lab.fault(order, args.delay_ms)
        lab.cancel(actor, order)
    lab.app_overrides['a'] = options(mode, schedulers, args.workers, args.capacity)
    lab.restart_app('a')
    assert lab.metrics('a')['synchronousRefundScan'] == (mode == 'inline')
    wait(lambda: lab.ledger(slow[0][2])['actualRefunds'] == 1,
         description='first slow provider call committed; response is delayed')
    # Same explicit expiry fixture in every cell. No restart/startup time in t0.
    lab.sql('UPDATE orders SET order_time=DATE_SUB(NOW(),INTERVAL 16 MINUTE) WHERE id=%s', (unpaid[2]['id'],))
    started = time.perf_counter()
    lab.cancel(quick[0], quick[2])
    observed, samples = {}, []
    def done():
        snapshot = lab.one('''SELECT
            (SELECT status FROM orders WHERE id=%s) AS expiredStatus,
            (SELECT status FROM refund_task WHERE order_id=%s) AS quickStatus,
            (SELECT COUNT(*) FROM refund_task WHERE status<>'SUCCEEDED' AND order_id IN ('''
                           + ','.join(['%s'] * len(slow)) + ')) AS slowPending',
                           (unpaid[2]['id'], quick[2]['id']) + tuple(job[2]['id'] for job in slow))
        slow_pending = snapshot.pop('slowPending') > 0
        elapsed = round((time.perf_counter() - started) * 1000, 2)
        samples.append(dict(elapsedMs=elapsed, slowPending=slow_pending, **snapshot))
        for label, complete in [('expiry', snapshot['expiredStatus'] == 6), ('quickRefund', snapshot['quickStatus'] == 'SUCCEEDED')]:
            if label not in observed and complete:
                observed[label] = dict(ms=elapsed, completedWhileSlowPending=slow_pending)
        return len(observed) == 2
    wait(done, timeout=args.delay_ms / 1000 * slow_count + 12, description='expiry and ordinary refund finish')
    wait(lambda: all(lab.task(job[2])['status'] == 'SUCCEEDED' for job in slow), timeout=10, description='slow refunds finish')
    metrics = lab.metrics('a')
    result = dict(round=round_index, mode=mode, schedulerThreads=schedulers,
                  slowCount=slow_count, workers=args.workers, queueCapacity=args.capacity,
                  scanMs=200, delayMs=args.delay_ms, observed=observed,
                  workerRejected=metrics['refundExecutor']['rejected'], sampleCount=len(samples))
    # Causal observations, not an arbitrary production latency SLA.
    expiry_independent = mode == 'async' or schedulers > 1
    refund_independent = mode == 'async' and slow_count < args.workers
    lab.check('ablation ' + str(round_index) + ' ' + mode + '/scheduler' + str(schedulers) + '/slow' + str(slow_count),
              observed['expiry']['completedWhileSlowPending'] == expiry_independent
              and observed['quickRefund']['completedWhileSlowPending'] == refund_independent, result)
    assert all(lab.ledger(job[2])['actualRefunds'] == 1 for job in slow + [quick])
    assert lab.scalar('SELECT reserved_count FROM meal_session WHERE id=%s', (unpaid[1]['id'],)) == 0
    lab.invariants()
    (lab.directory / ('ablation-' + str(round_index) + '-' + mode + '-' + str(schedulers) + '-' + str(slow_count) + '.json')).write_text(
        json.dumps(dict(result, samples=samples), indent=2), encoding='utf-8')
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--rounds', type=int, default=3)
    parser.add_argument('--delay-ms', type=int, default=5000)
    parser.add_argument('--workers', type=int, default=2)
    parser.add_argument('--capacity', type=int, default=2)
    parser.add_argument('--seed', type=int, default=20261019)
    args = parser.parse_args()
    assert 1 <= args.rounds <= 5 and 3000 <= args.delay_ms <= 5000
    assert 2 <= args.workers <= 4 and 1 <= args.capacity <= 16
    lab = Lab()
    report = dict(date=datetime.now().astimezone().isoformat(), status='running', checks=lab.checks,
                  parameters=vars(args), jarSha256=hashlib.sha256(lab.jar.read_bytes()).hexdigest(), runs=[])
    try:
        lab.setup()
        report['environment'] = dict(lab.environment_info(), scheduledInstances=1,
                                     refundScanMs=200, timeoutScanMs=200, leaseSeconds=30,
                                     instrumentedStateAudit=False, providerDelayIsPostCommit=True)
        rng = random.Random(args.seed)
        for round_index in range(1, args.rounds + 1):
            # Full factorial with spare capacity, plus two saturation boundary cells.
            plan = [(mode, schedulers, 1) for mode, schedulers in VARIANTS]
            plan += [('async', 1, args.workers), ('async', 2, args.workers)]
            rng.shuffle(plan)
            for mode, schedulers, count in plan:
                print('ABLATION round=' + str(round_index) + ' ' + mode + '/scheduler' + str(schedulers) + '/slow' + str(count), flush=True)
                report['runs'].append(run_case(lab, mode, schedulers, count, args, round_index))
        summary = []
        for mode, schedulers, count in sorted({(row['mode'], row['schedulerThreads'], row['slowCount']) for row in report['runs']}):
            rows = [row for row in report['runs'] if (row['mode'], row['schedulerThreads'], row['slowCount']) == (mode, schedulers, count)]
            summary.append(dict(mode=mode, schedulerThreads=schedulers, slowCount=count, repetitions=len(rows),
                                expiryMedianMs=statistics.median(row['observed']['expiry']['ms'] for row in rows),
                                quickRefundMedianMs=statistics.median(row['observed']['quickRefund']['ms'] for row in rows)))
        report['summary'] = summary
        report['status'] = 'passed'
    except Exception as error:
        report['failure'] = type(error).__name__ + ': ' + str(error)
        report['status'] = 'failed'
        raise
    finally:
        report['finishedAt'] = datetime.now().astimezone().isoformat()
        (lab.directory / 'ablation-report.json').write_text(json.dumps(report, indent=2, default=str), encoding='utf-8')
        print('Ablation report: ' + str(lab.directory / 'ablation-report.json'), flush=True)
        lab.close()


if __name__ == '__main__':
    main()
