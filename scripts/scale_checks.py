"""Large synthetic tables: query plans and warm-cache reads, never provider operations.
All rows are explicitly tagged fixtures in a unique lab schema. No real funds or normal DB writes.
"""
import argparse
import hashlib
import json
import re
import time
from datetime import datetime, timedelta

from reliability_lab import Lab, ROOT
from reliability_checks import percentile


def mapper_select(relative, method):
    source = (ROOT / relative).read_text(encoding='utf-8')
    end = source.index('List<Long> ' + method + '(')
    start = source.rfind('@Select(', 0, end)
    assert start >= 0
    literals = re.findall(r'"(?:\\.|[^"\\])*"', source[start:end])
    return ''.join(json.loads(value) for value in literals)


def bind(query, values):
    arguments = []
    def parameter(match):
        arguments.append(values[match.group(1)])
        return '%s'
    return re.sub(r'#\{(\w+)\}', parameter, query), tuple(arguments)


PROPOSED_TIMEOUT = (
    '(select id from orders where status=1 and pay_status=0 and order_time<=#{before} '
    'order by order_time,id limit 100) union all '
    '(select o.id from meal_session m join orders o on o.meal_session_id=m.id '
    'where m.cutoff_time<=#{now} and o.status=1 and o.pay_status=0 and o.order_time>#{before} '
    'order by m.cutoff_time,o.id limit 100) limit 100')


def seed(lab, rows):
    actor = lab.actor()
    now = datetime.now().replace(microsecond=0)
    groups = {'SUCCEEDED': rows - 10000, 'FAILED': 5000, 'PENDING_FUTURE': 3000,
              'PENDING_DUE': 500, 'PROCESSING_EXPIRED': 500, 'PROCESSING_FUTURE': 1000}
    records, refunds = [], []
    index = 0
    for group, count in groups.items():
        for _ in range(count):
            number = 'scale-' + lab.run + '-' + str(index)
            status = group.split('_')[0]
            paid = 2 if status == 'SUCCEEDED' else 1
            refund_status = status if status in ('SUCCEEDED', 'FAILED') else 'PENDING'
            records.append((number, 6, actor['id'], 1, now - timedelta(days=30), paid, 10,
                            'scale-fixture', 'scale-' + str(index), refund_status))
            refunds.append((number, status, group))
            index += 1
    with lab.db() as db, db.cursor() as cursor:
        for offset in range(0, rows, 5000):
            cursor.executemany('INSERT INTO orders(number,status,user_id,address_book_id,order_time,pay_status,amount,remark,request_key,refund_status) '
                               'VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)', records[offset:offset + 5000])
            if offset % 20000 == 0:
                print('Seeding synthetic orders: ' + str(offset + 5000), flush=True)
        cursor.execute("SELECT id,number FROM orders WHERE remark='scale-fixture'")
        identifiers = {number: identifier for identifier, number in cursor.fetchall()}
        tasks = []
        for number, status, group in refunds:
            next_time = now + timedelta(days=1) if group == 'PENDING_FUTURE' else now - timedelta(minutes=1)
            lease = now + timedelta(days=1) if group == 'PROCESSING_FUTURE' else now - timedelta(minutes=1)
            tasks.append((identifiers[number], 'R' + number, status, next_time,
                          lease if status == 'PROCESSING' else None, now, now))
        for offset in range(0, rows, 5000):
            cursor.executemany('INSERT INTO refund_task(order_id,refund_number,status,next_retry_time,lease_until,create_time,update_time) '
                               'VALUES(%s,%s,%s,%s,%s,%s,%s)', tasks[offset:offset + 5000])
            if offset % 20000 == 0:
                print('Seeding synthetic refund tasks: ' + str(offset + 5000), flush=True)
    future = lab.meal(5000)
    past = lab.meal(5000)
    # Query fixtures only; no assertion of naturally elapsed business time.
    lab.sql('UPDATE meal_session SET cutoff_time=%s WHERE id=%s', (now - timedelta(hours=1), past['id']))
    pending = []
    for index in range(5000):
        group = 'not-due' if index < 4000 else ('aged' if index < 4500 else 'cutoff')
        session = past['id'] if group == 'cutoff' else future['id']
        pending.append(('expiry-' + lab.run + '-' + str(index), actor['id'], now - timedelta(minutes=5),
                        'scale-expiry-' + group, session, 'sf' + str(index).zfill(20)))
    with lab.db() as db, db.cursor() as cursor:
        cursor.executemany('INSERT INTO orders(number,status,user_id,address_book_id,order_time,pay_status,amount,remark,fulfillment_type,meal_session_id,reserved_quantity,pickup_code) '
                           'VALUES(%s,1,%s,NULL,%s,0,10,%s,2,%s,1,%s)', pending)
    lab.sql('UPDATE meal_session SET reserved_count=(SELECT COUNT(*) FROM orders WHERE meal_session_id=meal_session.id AND status<>6)')
    lab.sql('ANALYZE TABLE orders,refund_task,meal_session')
    lab.invariants()
    return {'syntheticRefundTasks': rows, 'syntheticOrders': rows + 5000, 'refundDistribution': groups,
            'pendingUnpaidOrders': 5000, 'onlyCutoffInitiallyDue': 500, 'fixturesNeverSentToProvider': True}


def measure(lab, query, arguments, repeats):
    with lab.db() as db, db.cursor() as cursor:
        for _ in range(3):
            cursor.execute(query, arguments)
            cursor.fetchall()
        def counters():
            cursor.execute("SHOW SESSION STATUS WHERE Variable_name IN ('Handler_read_key','Handler_read_next','Handler_read_rnd_next')")
            return {key: int(value) for key, value in cursor.fetchall()}
        before = counters()
        times = []
        selected = []
        for _ in range(repeats):
            started = time.perf_counter()
            cursor.execute(query, arguments)
            selected = [row[0] for row in cursor.fetchall()]
            times.append((time.perf_counter() - started) * 1000)
        after = counters()
    result = {'repeats': repeats, 'warmupQueries': 3, 'p50Ms': percentile(times, .5), 'p95Ms': percentile(times, .95),
              'returnedRows': len(selected),
              'handlerReadsPerCall': {key: round((after[key] - value) / repeats, 2) for key, value in before.items()},
              'explain': lab.sql('EXPLAIN ' + query, arguments),
              'explainAnalyze': list(lab.sql('EXPLAIN ANALYZE ' + query, arguments)[0].values())[0]}
    assert len(selected) == len(set(selected))
    return result, selected


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--rows', type=int, default=100000)
    parser.add_argument('--repeats', type=int, default=20)
    args = parser.parse_args()
    assert 20000 <= args.rows <= 500000 and 5 <= args.repeats <= 100
    lab = Lab()
    report = {'date': datetime.now().astimezone().isoformat(), 'status': 'running', 'checks': lab.checks,
              'parameters': vars(args), 'queryResults': {}, 'jarSha256': hashlib.sha256(lab.jar.read_bytes()).hexdigest()}
    try:
        lab.setup()
        report['environment'] = lab.environment_info()
        report['fixture'] = seed(lab, args.rows)
        timeout = mapper_select('sky-take-out/sky-server/src/main/java/com/sky/mapper/OrderMapper.java', 'findExpired')
        due = mapper_select('sky-take-out/sky-server/src/main/java/com/sky/mapper/RefundTaskMapper.java', 'due')
        now = datetime.now()
        values = {'now': now, 'before': now - timedelta(minutes=15)}
        for label, query in [('refundCandidates', due), ('timeoutCurrentCutoffOnly', timeout), ('timeoutSplitCutoffOnly', PROPOSED_TIMEOUT)]:
            result, identifiers = measure(lab, *bind(query, values), args.repeats)
            report['queryResults'][label] = result
            if label == 'refundCandidates':
                valid = lab.scalar("SELECT COUNT(*) FROM refund_task WHERE id IN (" + ','.join(['%s'] * len(identifiers)) + ") AND "
                                   "((status='PENDING' AND next_retry_time<=%s) OR (status='PROCESSING' AND lease_until<%s))",
                                   tuple(identifiers) + (now, now))
                lab.check('large refund candidate query returns bounded eligible unique tasks', valid == len(identifiers) == 20)
            else:
                valid = lab.scalar("SELECT COUNT(*) FROM orders WHERE id IN (" + ','.join(['%s'] * len(identifiers)) + ") AND remark='scale-expiry-cutoff'", tuple(identifiers))
                lab.check(label + ' returns only expired unpaid fixture orders', valid == len(identifiers) == 100)
        lab.sql("UPDATE orders SET order_time=DATE_SUB(NOW(),INTERVAL 16 MINUTE) WHERE remark='scale-expiry-aged'")
        for label, query in [('timeoutCurrentMixed', timeout), ('timeoutSplitMixed', PROPOSED_TIMEOUT)]:
            result, identifiers = measure(lab, *bind(query, values), args.repeats)
            report['queryResults'][label] = result
            lab.check(label + ' has a full batch without duplicates', len(identifiers) == 100)
        report['status'] = 'passed'
    except Exception as error:
        report['status'] = 'failed'
        report['failure'] = type(error).__name__ + ': ' + str(error)
        raise
    finally:
        report['finishedAt'] = datetime.now().astimezone().isoformat()
        (lab.directory / 'scale-report.json').write_text(json.dumps(report, indent=2, default=str), encoding='utf-8')
        print('Scale report: ' + str(lab.directory / 'scale-report.json'), flush=True)
        lab.close()


if __name__ == '__main__':
    main()
