"""Repeated warmup/steady loads and replayable seeded input races, all in owned labs.
Fault-phase disconnects are reported separately from steady-load system errors.
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
from background_checks import scheduled_options
from reliability_lab import Lab, wait
from reliability_checks import sustained


def random_race(lab, seed, count=48):
    rng = random.Random(seed)
    lab.app_overrides = {name: scheduled_options()[:-1] + ['--sky.refund.lease-seconds=5'] for name in ('a', 'b')}
    lab.restart_app('a')
    lab.restart_app('b')
    meal = lab.meal(count)
    jobs, inputs = [], []
    for index in range(count):
        actor = lab.actor()
        lab.cart(actor)
        body = lab.payload(meal)
        order = lab.submit(actor, body)
        if index % 2 == 0:
            # Disclosed timeout fixture, not evidence of a naturally elapsed 15-minute timeout.
            lab.sql('UPDATE orders SET order_time=DATE_SUB(NOW(),INTERVAL 16 MINUTE) WHERE id=%s', (order['id'],))
        operations = ['pay', 'pay', 'pay', 'cancel', 'cancel', 'cancel', 'expire', 'expire', 'replay']
        rng.shuffle(operations)
        plan = [{'operation': value, 'instance': rng.choice(['a', 'b']), 'delayMs': rng.randrange(5, 40)} for value in operations]
        if index % 8 == 0:
            lab.fault(order, 1500)
        jobs.append((actor, order, body, plan))
        inputs.append({'job': index, 'orderId': order['id'], 'orderNumber': order['orderNumber'],
                       'requestKey': body['requestKey'], 'agedFixture': index % 2 == 0, 'operations': plan})
    (lab.directory / ('race-input-' + str(seed) + '.json')).write_text(json.dumps(inputs, indent=2), encoding='utf-8')
    print('RACE seed=' + str(seed) + ': prepared ' + str(count) + ' orders; starting concurrent operations', flush=True)
    begin = threading.Barrier(count + 1)
    observed, guard = [], threading.Lock()
    def call_operation(actor, order, body, operation, instance):
        if operation == 'pay':
            return lab.call(instance, 'POST', '/user/local/orders/' + order['orderNumber'] + '/callback',
                            {'paidAmount': order['orderAmount']}, actor=actor, expected=None)
        if operation == 'cancel':
            return lab.call(instance, 'PUT', '/user/order/cancel/' + str(order['id']), actor=actor, expected=None)
        if operation == 'expire':
            return lab.call(instance, 'POST', '/admin/local/timeouts/' + str(order['id']), admin=True, expected=None)
        result = lab.call(instance, 'POST', '/user/order/submit', body, actor=actor, expected=None)
        if result['_http'] == 200 and result.get('code') == 1:
            assert result['data']['id'] == order['id']
        return result
    def worker(job):
        actor, order, body, plan = job
        begin.wait()
        for entry in plan:
            time.sleep(entry['delayMs'] / 1000)
            for attempt in range(3):
                instance = entry['instance'] if attempt == 0 else 'b'
                try:
                    result = call_operation(actor, order, body, entry['operation'], instance)
                    kind = 'success' if result['_http'] == 200 and result.get('code') == 1 else (
                        'business_rejection' if result['_http'] == 200 and result.get('code') == 0 else 'system_error')
                    with guard:
                        observed.append({'orderId': order['id'], 'operation': entry['operation'], 'attempt': attempt + 1,
                                         'instance': instance, 'kind': kind, 'http': result['_http']})
                    if kind != 'system_error':
                        break
                except requests.RequestException as error:
                    with guard:
                        observed.append({'orderId': order['id'], 'operation': entry['operation'], 'attempt': attempt + 1, 'instance': instance,
                                         'kind': 'transport_error', 'exception': type(error).__name__})
                    lab.capture_failure('race')
                time.sleep(.05)
            else:
                raise AssertionError('Fault-phase operation did not recover using its original order/key')
        # Idempotent final observations repair an uncertain callback; same persisted number, never a new order.
        lab.pay(actor, order, 'b')
        lab.cancel(actor, order, 'b')
    started = time.perf_counter()
    with futures.ThreadPoolExecutor(count) as pool:
        pending = [pool.submit(worker, job) for job in jobs]
        begin.wait()
        time.sleep(.12)
        lab.stop(lab.apps['a'])  # Only this lab's Popen handle. B keeps serving/scanning.
        for future in pending:
            future.result(timeout=40)
    lab.start_app('a')
    wait(lambda: all(lab.task(order)['status'] == 'SUCCEEDED' for _, order, _, _ in jobs), timeout=40,
         description='all mixed-race refund intents converge automatically')
    ledger = requests.get(lab.provider + '/ledger', timeout=10).json()
    numbers = {order['orderNumber'] for _, order, _, _ in jobs}
    paid = [row for row in ledger['payments'] if row['number'] in numbers]
    refunded = [row for row in ledger['refunds'] if row['number'] in numbers]
    refund_calls = sum(row['kind'] == 'refund' and row['number'] in numbers for row in ledger['calls'])
    lab.check('seed ' + str(seed) + ': payments/cancels/expiry/replay/restart converge with one refund per order',
              len(paid) == len(refunded) == count
              and all(lab.scalar('SELECT status=6 AND pay_status=2 AND refund_status=\'SUCCEEDED\' FROM orders WHERE id=%s', (order['id'],)) for _, order, _, _ in jobs)
              and lab.scalar('SELECT reserved_count FROM meal_session WHERE id=%s', (meal['id'],)) == 0)
    lab.invariants()
    counts = {kind: sum(item['kind'] == kind for item in observed) for kind in
              ('success', 'business_rejection', 'system_error', 'transport_error')}
    result = {'seed': seed, 'orders': count, 'operationInputs': len(inputs) * 9, 'attemptOutcomes': counts,
              'intentionalProcessKills': 1, 'actualPayments': len(paid), 'actualRefunds': len(refunded),
              'externalRefundCalls': refund_calls, 'elapsedIncludingRestartSeconds': round(time.perf_counter() - started, 2),
              'refundAutoScan': True, 'refundScanMs': 200, 'timeoutScanMs': 200, 'leaseSeconds': 5,
              'finalBusinessDataVerified': True, 'replayScope': 'seed reproduces inputs, not exact OS thread interleaving'}
    (lab.directory / ('race-outcomes-' + str(seed) + '.json')).write_text(json.dumps(observed, indent=2), encoding='utf-8')
    print('Race ' + json.dumps(result), flush=True)
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--seconds', type=float, default=120)
    parser.add_argument('--rounds', type=int, default=3)
    parser.add_argument('--warmup', type=float, default=10)
    parser.add_argument('--workers', type=int, default=16)
    parser.add_argument('--pool', type=int, default=10)
    parser.add_argument('--scenarios', default='hot_session,spread_sessions')
    parser.add_argument('--seeds', default='20261015,20261016,20261017')
    parser.add_argument('--race-only', action='store_true')
    parser.add_argument('--skip-races', action='store_true')
    parser.add_argument('--port-mode', choices=['dynamic', 'low'], default='dynamic')
    args = parser.parse_args()
    scenarios = args.scenarios.split(',')
    assert all(value in ('hot_session', 'spread_sessions', 'same_user_replay') for value in scenarios)
    assert 1 <= args.seconds <= 600 and 1 <= args.warmup <= 60 and 1 <= args.rounds <= 10
    assert 2 <= args.workers <= 64 and 1 <= args.pool <= 32
    lab = Lab(port_mode=args.port_mode)
    lab.pool = args.pool
    lab.network_diagnostics = True
    report = {'date': datetime.now().astimezone().isoformat(), 'status': 'running', 'checks': lab.checks,
              'parameters': vars(args), 'warmups': [], 'loadRuns': [], 'races': [],
              'jarSha256': hashlib.sha256(lab.jar.read_bytes()).hexdigest()}
    try:
        lab.setup()
        report['environment'] = lab.environment_info()
        if not args.race_only:
            for round_index in range(1, args.rounds + 1):
                # Alternate ordering to reduce consistently assigning later/JIT-warmed execution to one scenario.
                for scenario in (scenarios if round_index % 2 else list(reversed(scenarios))):
                    warm = sustained(lab, scenario, args.warmup, args.workers)
                    warm['round'] = round_index
                    report['warmups'].append(warm)
                    result = sustained(lab, scenario, args.seconds, args.workers)
                    result['round'] = round_index
                    report['loadRuns'].append(result)
                    print('MEASURED round=' + str(round_index) + ' scenario=' + scenario, flush=True)
        if not args.skip_races:
            for seed in [int(value) for value in args.seeds.split(',')]:
                report['races'].append(random_race(lab, seed))
        report['status'] = 'passed'
    except Exception as error:
        report['status'] = 'failed'
        report['failure'] = type(error).__name__ + ': ' + str(error)
        raise
    finally:
        report['finishedAt'] = datetime.now().astimezone().isoformat()
        (lab.directory / 'endurance-report.json').write_text(json.dumps(report, indent=2, default=str), encoding='utf-8')
        print('Endurance report: ' + str(lab.directory / 'endurance-report.json'), flush=True)
        lab.close()


if __name__ == '__main__':
    main()
