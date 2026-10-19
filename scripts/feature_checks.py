"""Real HTTP/MySQL checks for the LOCAL campus extension. Never target a shared environment.

Fault fixtures modify only this run's orders/sessions; the selected menu row is restored.
Concurrency figures describe a small local correctness run, not a capacity benchmark.
"""
import argparse
import concurrent.futures as futures
import datetime as dt
from decimal import Decimal
import json
import os
import socket
import statistics
import time
import uuid
import base64

import smoke as s

CHECKS, BATCHES = [], []
REPORT = s.LOCAL / 'feature-report.json'
ADMIN = {}


def call(method, path, payload=None, auth=None, expected=1):
    headers = dict(auth or {})
    if payload is not None:
        headers['Content-Type'] = 'application/json'
    status, body = s.raw(method, path, None if payload is None else json.dumps(payload).encode(), headers)
    assert status == 200, f'{method} {path}: HTTP {status}'
    response = json.loads(body)
    if expected is not None:
        assert response['code'] == expected, f'{method} {path}: {response}'
    return response


def check(label, condition=True):
    assert condition, label
    CHECKS.append({'check': label, 'passed': True})
    print('PASS ' + label, flush=True)


def actor(code):
    user = call('POST', '/user/user/login', {'code': code})['data']
    return {'id': user['id'], 'auth': {'authentication': user['token']}, 'code': code}


def cart(user, dish, quantity=1):
    call('DELETE', '/user/shoppingCart/clean', auth=user['auth'])
    for _ in range(quantity):
        call('POST', '/user/shoppingCart/add', {'dishId': dish['id']}, user['auth'])


def payload(session, marker, key=None):
    return {'requestKey': key or uuid.uuid4().hex, 'mealSessionId': session['id'],
            'contactName': '本地测试', 'contactPhone': '13800000000', 'remark': marker,
            'amount': '0.01', 'packAmount': -999, 'payMethod': 1}


def submit(user, request):
    return call('POST', '/user/order/submit', request, user['auth'])['data']


def detail(user, order):
    return call('GET', '/user/order/orderDetail/' + str(order['id']), auth=user['auth'])['data']


def callback(user, order, expected=1, amount=None):
    return call('POST', '/user/local/orders/' + order['orderNumber'] + '/callback',
                {'paidAmount': order['orderAmount'] if amount is None else amount}, user['auth'], expected)


def session(marker, capacity):
    now = dt.datetime.fromisoformat(call('GET', '/api/meal/time', auth=ADMIN)['data'])
    day = now.date() + dt.timedelta(days=1)
    value = {'title': marker, 'mealDate': day.isoformat(), 'capacity': capacity,
             'cutoffTime': str(day) + ' 10:30:00', 'pickupStart': str(day) + ' 11:30:00',
             'pickupEnd': str(day) + ' 13:00:00'}
    return call('POST', '/api/meal/sessions', value, ADMIN)['data']


def batch(label, count, operation):
    barrier = __import__('threading').Barrier(count)
    def invoke(index):
        barrier.wait(timeout=30)
        started = time.perf_counter()
        response = operation(index)
        return response, (time.perf_counter() - started) * 1000
    started = time.perf_counter()
    with futures.ThreadPoolExecutor(max_workers=count) as pool:
        results = list(pool.map(invoke, range(count)))
    durations = sorted(item[1] for item in results)
    responses = [item[0] for item in results]
    BATCHES.append({'scenario': label, 'requests': count, 'simultaneous_workers': count,
                    'elapsed_ms': round((time.perf_counter() - started) * 1000, 2),
                    'p50_ms': round(statistics.median(durations), 2),
                    'p95_ms': round(durations[max(0, int(count * .95 + .999) - 1)], 2),
                    'success_responses': sum(item['code'] == 1 for item in responses),
                    'business_rejections': sum(item['code'] == 0 for item in responses)})
    return responses


def ws(token=None):
    connection = socket.create_connection(('127.0.0.1', 8081), timeout=8)
    connection.settimeout(8)
    key = base64.b64encode(os.urandom(16)).decode()
    protocol = '' if token is None else 'Sec-WebSocket-Protocol: sky-admin, auth.' + token + '\r\n'
    connection.sendall((f'GET /ws/feature-{uuid.uuid4().hex} HTTP/1.1\r\nHost: 127.0.0.1:8081\r\n'
                        f'Upgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Key: {key}\r\n'
                        'Sec-WebSocket-Version: 13\r\n' + protocol + '\r\n').encode())
    header = b''
    while b'\r\n\r\n' not in header:
        header += s.receive_exact(connection, 1)
    status = int(header.split(b'\r\n', 1)[0].split()[1])
    if status == 101:
        assert s.receive_event(connection)['type'] == 0, 'Endpoint registration was not acknowledged'
    return connection, status


def wait_refund(order_id, terminal='SUCCEEDED', timeout=15):
    deadline = time.monotonic() + timeout
    while True:
        value = s.sql(f'SELECT status FROM refund_task WHERE order_id={int(order_id)}')
        if value == terminal:
            return
        assert time.monotonic() < deadline, f'refund {order_id}: {value}, expected {terminal}'
        call('POST', '/api/local/refunds/run', auth=ADMIN)
        time.sleep(.15)


def main():
    run = uuid.uuid4().hex[:8]
    marker = 'feature-' + run
    ADMIN['token'] = call('POST', '/api/employee/login', {'username': 'admin', 'password': '123456'})['data']['token']
    call('PUT', '/api/shop/1', auth=ADMIN)
    dishes = call('GET', '/api/dish/page?page=1&pageSize=100', auth=ADMIN)['data']['records']
    dish = next(row for row in dishes if row['status'] == 1)
    u1, u2 = actor('local-test-' + run + '-a'), actor('local-test-' + run + '-b')
    meal = session(marker + '-main', 500)
    cart(u1, dish, 3)
    check('cart increments both number and amount', s.sql(f'SELECT number FROM shopping_cart WHERE user_id={u1["id"]}') == '3')
    s.sql(f"UPDATE shopping_cart SET amount=0.01,name='tampered-cart' WHERE user_id={u1['id']}")
    quote = call('GET', '/user/order/quote?mealSessionId=' + str(meal['id']), auth=u1['auth'])['data']
    expected = Decimal(str(dish['price'])) * 3 + Decimal('3')
    check('quote reprices tampered cart from current menu', Decimal(str(quote['amount'])) == expected)
    request = payload(meal, marker + '-idempotent')
    responses = batch('same user, same request key', 50,
                      lambda _: call('POST', '/user/order/submit', request, u1['auth'], expected=None))
    order = responses[0]['data']
    check('50 simultaneous retries return one persisted order',
          all(row['code'] == 1 and row['data']['id'] == order['id'] for row in responses)
          and s.sql(f"SELECT COUNT(*) FROM orders WHERE user_id={u1['id']} AND request_key='{request['requestKey']}'") == '1')
    check('idempotent retry reserves quantities only once', s.sql(f'SELECT reserved_count FROM meal_session WHERE id={meal["id"]}') == '3')
    check('server ignores client total and packaging fee', Decimal(str(order['orderAmount'])) == expected
          and order['packAmount'] == 3 and Decimal(str(order['deliveryFee'])) == 0)
    details = detail(u1, order)
    check('line snapshot is authoritative and quantity preserved', details['orderDetailList'][0]['name'] == dish['name']
          and details['orderDetailList'][0]['number'] == 3 and Decimal(str(details['orderDetailList'][0]['amount'])) == Decimal(str(dish['price'])))
    original_price = Decimal(str(dish['price']))
    try:
        s.sql(f'UPDATE dish SET price={original_price + 1} WHERE id={dish["id"]}')
        check('historical price snapshot survives catalog change', detail(u1, order)['orderDetailList'] == details['orderDetailList'])
    finally:
        s.sql(f'UPDATE dish SET price={original_price} WHERE id={dish["id"]}')
    call('POST', '/user/order/submit', dict(request, remark=marker + '-conflict'), u1['auth'], expected=0)
    check('same key with changed semantics is rejected')
    call('POST', '/user/order/submit', dict(request, requestKey='bad'), u1['auth'], expected=0)
    check('invalid request key is rejected')
    cart(u2, dish)
    try:
        s.sql(f'UPDATE dish SET status=0 WHERE id={dish["id"]}')
        call('POST', '/user/order/submit', payload(meal, marker + '-off-sale'), u2['auth'], expected=0)
        check('off-sale product refused without clearing cart or reserving capacity',
              s.sql(f'SELECT reserved_count FROM meal_session WHERE id={meal["id"]}') == '3'
              and s.sql(f'SELECT COUNT(*) FROM shopping_cart WHERE user_id={u2["id"]}') == '1')
    finally:
        s.sql(f'UPDATE dish SET status=1 WHERE id={dish["id"]}')

    # Object-level ownership; hiding the address in list alone is insufficient.
    address_data = {'consignee': marker, 'phone': '13800000000', 'sex': '1', 'provinceName': '湖北省',
                    'cityName': '武汉市', 'districtName': '洪山区', 'detail': '本地测试', 'label': '测试'}
    call('POST', '/user/addressBook', address_data, u1['auth'])
    address_id = call('GET', '/user/addressBook/list', auth=u1['auth'])['data'][0]['id']
    for method, path, value in [
        ('GET', '/user/addressBook/' + str(address_id), None),
        ('PUT', '/user/addressBook', dict(address_data, id=address_id, userId=u2['id'])),
        ('PUT', '/user/addressBook/default', {'id': address_id}),
        ('DELETE', '/user/addressBook? id=' + str(address_id), None)]:
        call(method, path.replace('? id=', '?id='), value, u2['auth'], expected=0)
    check('second user cannot read/update/default/delete another address',
          s.sql(f'SELECT user_id FROM address_book WHERE id={address_id}') == str(u1['id']))
    for method, path, value in [
        ('GET', '/user/order/orderDetail/' + str(order['id']), None),
        ('PUT', '/user/order/cancel/' + str(order['id']), None),
        ('POST', '/user/order/repetition/' + str(order['id']), None),
        ('GET', '/user/order/reminder/' + str(order['id']), None),
        ('PUT', '/user/order/payment', {'orderNumber': order['orderNumber'], 'payMethod': 1}),
        ('POST', '/user/local/orders/' + order['orderNumber'] + '/callback', {'paidAmount': order['orderAmount']})]:
        call(method, path, value, u2['auth'], expected=0)
    check('second user cannot access six order operations', s.sql(f'SELECT status FROM orders WHERE id={order["id"]}') == '1')
    for token in (None, u1['auth']['authentication']):
        connection, status = ws(token)
        connection.close()
        check('WebSocket refuses ' + ('anonymous' if token is None else 'user-token') + ' handshake', status == 401)
    callback(u1, order, expected=0, amount='0.01')
    check('wrong callback amount leaves order unpaid', s.sql(f'SELECT pay_status FROM orders WHERE id={order["id"]}') == '0')
    connection, status = ws(ADMIN['token'])
    try:
        check('admin-authenticated WebSocket upgrades', status == 101)
        callbacks = batch('duplicate payment callbacks', 20, lambda _: callback(u1, order))
        event = s.receive_event(connection)
        check('duplicate callbacks change state once and emit one event', all(row['code'] == 1 for row in callbacks)
              and int(event['orderId']) == int(order['id']) and event['type'] == 1
              and s.sql(f'SELECT status,pay_status FROM orders WHERE id={order["id"]}') == '2\t1')
        connection.settimeout(.4)
        try:
            unexpected = connection.recv(2048)
            assert not unexpected, 'Unexpected extra WebSocket frame: ' + unexpected.hex()
        except socket.timeout:
            pass
    finally:
        connection.close()
    accepts = batch('concurrent merchant acceptance', 20,
                    lambda _: call('PUT', '/api/order/confirm', {'id': order['id']}, ADMIN, expected=None))
    check('20 concurrent acceptances allow exactly one state transition', sum(row['code'] == 1 for row in accepts) == 1
          and s.sql(f'SELECT status FROM orders WHERE id={order["id"]}') == '3')
    call('PUT', '/api/order/complete/' + str(order['id']), auth=ADMIN, expected=0)
    call('PUT', '/api/order/delivery/' + str(order['id']), auth=ADMIN, expected=0)
    check('pickup cannot bypass dedicated fulfillment workflow')
    call('PUT', '/api/meal/orders/' + str(order['id']) + '/ready', auth=ADMIN)
    call('POST', '/api/meal/redeem', {'pickupCode': order['pickupCode']}, ADMIN, expected=0)
    check('pickup cannot be redeemed before its pickup window')
    preparation = call('GET', '/api/meal/sessions/' + str(meal['id']) + '/preparation', auth=ADMIN)['data']
    check('paid meal preparation aggregates quantities', any(row['quantity'] == 3 for row in preparation))

    # Limited capacity contested by distinct users, each with one portion.
    small = session(marker + '-contest', 5)
    actors = [actor('local-test-' + run + '-' + str(index)) for index in range(20)]
    for user in actors:
        cart(user, dish)
    requests = [payload(small, marker + '-contest-' + str(index)) for index in range(20)]
    contest = batch('20 distinct users contest 5 portions', 20,
                    lambda index: call('POST', '/user/order/submit', requests[index], actors[index]['auth'], expected=None))
    winners = [(actors[index], row['data']) for index, row in enumerate(contest) if row['code'] == 1]
    check('capacity 5 permits exactly 5 of 20 competing orders', len(winners) == 5
          and sum(row['code'] == 0 for row in contest) == 15
          and s.sql(f'SELECT reserved_count FROM meal_session WHERE id={small["id"]}') == '5')
    check('capacity losers keep their shopping carts', all(s.sql(f'SELECT number FROM shopping_cart WHERE user_id={actors[index]["id"]}') == '1'
                                                        for index, row in enumerate(contest) if row['code'] == 0))
    winner, winning_order = winners[0]
    cancels = batch('concurrent repeated cancellation', 10,
                   lambda _: call('PUT', '/user/order/cancel/' + str(winning_order['id']), auth=winner['auth']))
    check('repeated cancellation releases one portion once', all(row['code'] == 1 for row in cancels)
          and s.sql(f'SELECT reserved_count FROM meal_session WHERE id={small["id"]}') == '4')
    paid_user, paid_order = winners[1]
    callback(paid_user, paid_order)
    s.sql(f'UPDATE meal_session SET cutoff_time=DATE_SUB(NOW(),INTERVAL 1 SECOND) WHERE id={small["id"]}')
    call('PUT', '/user/order/cancel/' + str(paid_order['id']), auth=paid_user['auth'], expected=0)
    check('paid user cancellation blocked after meal cutoff', s.sql(f'SELECT status FROM orders WHERE id={paid_order["id"]}') == '2')
    call('POST', '/user/order/submit', payload(small, marker + '-past-cutoff'), u2['auth'], expected=0)
    check('booking refused after meal cutoff')
    late_user, late_order = winners[2]
    callback(late_user, late_order)
    wait_refund(late_order['id'])
    check('payment arriving after cutoff stays canceled and is refunded',
          s.sql(f'SELECT status,pay_status,refund_status FROM orders WHERE id={late_order["id"]}') == '6\t2\tSUCCEEDED')
    # Eliminate unexpired reservation fixtures for a precise timeout counter check.
    expired = session(marker + '-timeout', 3)
    cart(u2, dish)
    timed_order = submit(u2, payload(expired, marker + '-timeout'))
    s.sql(f'UPDATE orders SET order_time=DATE_SUB(NOW(),INTERVAL 16 MINUTE) WHERE id={timed_order["id"]}')
    call('POST', '/api/local/timeouts/' + str(timed_order['id']), auth=ADMIN)
    call('POST', '/api/local/timeouts/' + str(timed_order['id']), auth=ADMIN)
    check('unpaid timeout cancels once and releases reservation', s.sql(f'SELECT reserved_count FROM meal_session WHERE id={expired["id"]}') == '0'
          and s.sql(f'SELECT status,pay_status FROM orders WHERE id={timed_order["id"]}') == '6\t0')
    callback(u2, timed_order)
    wait_refund(timed_order['id'])
    check('late callback after timeout cannot resurrect order or release twice',
          s.sql(f'SELECT reserved_count FROM meal_session WHERE id={expired["id"]}') == '0'
          and s.sql(f'SELECT status,pay_status FROM orders WHERE id={timed_order["id"]}') == '6\t2')

    # Cart writer and checkout share the user's row lock. Total portions are conserved.
    cart(u2, dish)
    race_request = payload(meal, marker + '-cart-race')
    raced = batch('cart mutation versus checkout', 2, lambda index:
                  call('POST', '/user/order/submit', race_request, u2['auth']) if index == 0 else
                  call('POST', '/user/shoppingCart/add', {'dishId': dish['id']}, u2['auth']))
    race_order = raced[0]['data']
    portion_sum = s.sql(f'SELECT (SELECT SUM(number) FROM order_detail WHERE order_id={race_order["id"]}) + '
                       f'(SELECT COALESCE(SUM(number),0) FROM shopping_cart WHERE user_id={u2["id"]})')
    check('cart-versus-checkout race conserves two portions', portion_sum == '2')
    call('PUT', '/user/order/cancel/' + str(race_order['id']), auth=u2['auth'])
    cart(u2, dish)

    # Inject a detail failure; all four writes (capacity/header/detail/cart) must roll back.
    rollback_request = payload(meal, marker + '-rollback')
    capacity_before = s.sql(f'SELECT reserved_count FROM meal_session WHERE id={meal["id"]}')
    trigger = 'campus_probe_' + run
    try:
        s.sql(f"DELIMITER //\nCREATE TRIGGER {trigger} BEFORE INSERT ON order_detail FOR EACH ROW BEGIN "
              f"IF (SELECT remark FROM orders WHERE id=NEW.order_id)='{marker}-rollback' THEN "
              "SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='Campus rollback probe'; END IF; END//\nDELIMITER ;", administrative=True)
        status, _ = s.raw('POST', '/user/order/submit', json.dumps(rollback_request).encode(),
                          dict(u2['auth'], **{'Content-Type': 'application/json'}))
        check('detail failure rolls back capacity, order and cart together', status == 500
              and s.sql(f'SELECT reserved_count FROM meal_session WHERE id={meal["id"]}') == capacity_before
              and s.sql(f"SELECT COUNT(*) FROM orders WHERE request_key='{rollback_request['requestKey']}'") == '0'
              and s.sql(f'SELECT number FROM shopping_cart WHERE user_id={u2["id"]}') == '1')
    finally:
        s.sql('DROP TRIGGER IF EXISTS ' + trigger, administrative=True)

    # A durable refund intent survives a simulated network failure.
    refund_order = submit(u2, payload(meal, marker + '-refund-retry'))
    callback(u2, refund_order)
    call('POST', '/api/local/refunds/fail-next', {'count': 1}, ADMIN)
    call('PUT', '/user/order/cancel/' + str(refund_order['id']), auth=u2['auth'])
    wait_refund(refund_order['id'])
    refund_state = s.sql(f'SELECT COUNT(*),MAX(attempts),MAX(failures),MAX(refund_number) FROM refund_task WHERE order_id={refund_order["id"]}').split('\t')
    check('refund retry retains one intent and stable provider refund number', refund_state[0] == '1' and int(refund_state[1]) >= 2
          and int(refund_state[2]) == 1 and refund_state[3] == 'R' + refund_order['orderNumber'])

    # Fixture a crashed worker (expired lease). Concurrent runners must recover only once.
    cart(u2, dish)
    recovery_order = submit(u2, payload(meal, marker + '-lease-recovery'))
    callback(u2, recovery_order)
    call('PUT', '/user/order/cancel/' + str(recovery_order['id']), auth=u2['auth'])
    # Scheduler may already finish; restore this local simulated task to a crashed-worker fixture.
    s.sql(f"UPDATE orders SET pay_status=1,refund_status='PENDING' WHERE id={recovery_order['id']};"
          f"UPDATE refund_task SET status='PROCESSING',lease_until=DATE_ADD(NOW(),INTERVAL 1 MINUTE),"
          f"lease_token='crashed-local-worker',provider_submitted=1 WHERE order_id={recovery_order['id']}")
    before_attempts = int(s.sql(f'SELECT attempts FROM refund_task WHERE order_id={recovery_order["id"]}'))
    s.sql(f'UPDATE refund_task SET lease_until=DATE_SUB(NOW(),INTERVAL 1 MINUTE) WHERE order_id={recovery_order["id"]}')
    batch('concurrent refund workers after lease expiry', 10,
          lambda _: call('POST', '/api/local/refunds/run', auth=ADMIN))
    wait_refund(recovery_order['id'])
    check('expired worker lease recovered once with a new claim token',
          int(s.sql(f'SELECT attempts FROM refund_task WHERE order_id={recovery_order["id"]}')) == before_attempts + 1
          and s.sql(f'SELECT lease_token FROM refund_task WHERE order_id={recovery_order["id"]}') != 'crashed-local-worker')

    # Six failed attempts -> visible manual intervention, then successful manual retry.
    cart(u2, dish)
    failed_order = submit(u2, payload(meal, marker + '-manual-retry'))
    callback(u2, failed_order)
    call('POST', '/api/local/refunds/fail-next', {'count': 6}, ADMIN)
    call('PUT', '/user/order/cancel/' + str(failed_order['id']), auth=u2['auth'])
    for _ in range(8):
        if s.sql(f'SELECT status FROM refund_task WHERE order_id={failed_order["id"]}') == 'FAILED':
            break
        s.sql(f"UPDATE refund_task SET next_retry_time=DATE_SUB(NOW(),INTERVAL 1 SECOND) "
              f"WHERE order_id={failed_order['id']} AND status='PENDING'")
        call('POST', '/api/local/refunds/run', auth=ADMIN)
    check('six refund failures become visible FAILED intent', s.sql(f'SELECT status,failures FROM refund_task WHERE order_id={failed_order["id"]}') == 'FAILED\t6'
          and s.sql(f'SELECT refund_status,pay_status FROM orders WHERE id={failed_order["id"]}') == 'FAILED\t1')
    task_id = int(s.sql(f'SELECT id FROM refund_task WHERE order_id={failed_order["id"]}'))
    call('POST', '/api/refunds/' + str(task_id) + '/retry', {'reason': '隔离测试：核实后重试原退款'}, auth=ADMIN)
    wait_refund(failed_order['id'])
    check('admin can retry failed refund without creating another task',
          s.sql(f'SELECT COUNT(*) FROM refund_task WHERE order_id={failed_order["id"]}') == '1'
          and s.sql(f'SELECT refund_status,pay_status FROM orders WHERE id={failed_order["id"]}') == 'SUCCEEDED\t2')

    # Move only this fixture's session into an actual current pickup window.
    s.sql(f"UPDATE meal_session SET meal_date=CURDATE(),cutoff_time=DATE_SUB(NOW(),INTERVAL 5 SECOND),"
          f"pickup_start=DATE_SUB(NOW(),INTERVAL 1 SECOND),pickup_end=DATE_ADD(NOW(),INTERVAL 1 MINUTE) WHERE id={meal['id']}")
    redemptions = batch('concurrent pickup-code redemption', 20,
                       lambda _: call('POST', '/api/meal/redeem', {'pickupCode': order['pickupCode']}, ADMIN, expected=None))
    check('20 concurrent redemptions complete exactly once', sum(row['code'] == 1 for row in redemptions) == 1
          and s.sql(f'SELECT status FROM orders WHERE id={order["id"]}') == '5')
    call('POST', '/api/meal/redeem', {'pickupCode': 'unknown-code'}, ADMIN, expected=0)
    check('invalid pickup code refused')
    check('database reservation invariants hold for every session', s.sql('SELECT COUNT(*) FROM meal_session WHERE reserved_count<0 OR reserved_count>capacity') == '0')
    check('Flyway migrations V2-V6 each recorded once', s.sql("SELECT COUNT(*) FROM flyway_schema_history WHERE version IN ('2','3','4','5','6') AND success=1") == '5')
    return {'user_code': u1['code'], 'request': request, 'order': order,
            'order_count_at_save': int(s.sql('SELECT COUNT(*) FROM orders'))}


def replay():
    report = json.loads(REPORT.read_text(encoding='utf-8'))
    saved = report['restart_fixture']
    user = actor(saved['user_code'])
    replayed = submit(user, saved['request'])
    check('idempotent result survives full service restart and closed meal', replayed['id'] == saved['order']['id']
          and replayed['orderNumber'] == saved['order']['orderNumber']
          and s.sql(f"SELECT COUNT(*) FROM orders WHERE user_id={user['id']} AND request_key='{saved['request']['requestKey']}'") == '1')
    check('restart and repeat bootstrap preserve existing business rows', int(s.sql('SELECT COUNT(*) FROM orders')) >= saved['order_count_at_save'])
    check('repeat startup preserves five successful schema migrations', s.sql("SELECT COUNT(*) FROM flyway_schema_history WHERE version IN ('2','3','4','5','6') AND success=1") == '5')
    report['checks'].extend(CHECKS)
    report['restart_verified'] = True
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--replay-only', action='store_true')
    options = parser.parse_args()
    if options.replay_only:
        replay()
    else:
        report = {'profile': 'local', 'external_payment': 'simulated', 'checks': CHECKS, 'concurrency_runs': BATCHES}
        try:
            report['restart_fixture'] = main()
            report['passed'] = True
        except Exception as error:
            report['passed'] = False
            report['failure'] = str(error)
            raise
        finally:
            report['executed_at'] = dt.datetime.now(dt.timezone(dt.timedelta(hours=8))).isoformat()
            REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(f'PASS {len(CHECKS)} campus checks', flush=True)
