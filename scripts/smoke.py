"""Exercise the LOCAL demo through Nginx and verify persisted business outcomes."""
import base64
import datetime as dt
import json
import os
import pathlib
import re
import socket
import struct
import subprocess
import urllib.error
import urllib.request
import uuid
import time
import zipfile

ROOT = pathlib.Path(__file__).resolve().parents[1]
RUNTIME = pathlib.Path(os.environ.get('SKY_RUNTIME_DIR', ROOT.parent / '.runtime'))
LOCAL = ROOT / '.local'
BASE = 'http://127.0.0.1:8081'
RESULTS = []
ADMIN = {}
USER = {}

def raw(method, path, body=None, headers=None):
    request = urllib.request.Request(BASE + path, data=body, headers=headers or {}, method=method)
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as error:
        return error.code, error.read()

def api(label, method, path, data=None, headers=None, code=1, status=200):
    request_headers = dict(headers or {})
    body = None
    if data is not None:
        request_headers['Content-Type'] = 'application/json'
        body = json.dumps(data, ensure_ascii=False).encode('utf-8')
    actual_status, content = raw(method, path, body, request_headers)
    assert actual_status == status, f'{label}: HTTP {actual_status}, expected {status}'
    response = json.loads(content) if content else {}
    if status == 200:
        assert response.get('code') == code, f'{label}: {response.get("msg")}'
    RESULTS.append({'check': label, 'passed': True})
    print('PASS ' + label, flush=True)
    return response.get('data')

def sql(statement, administrative=False):
    executable = next(RUNTIME.glob('mysql-*-winx64')) / 'bin' / 'mysql.exe'
    configuration = RUNTIME / 'mysql-client.cnf'
    if administrative:
        secrets = json.loads((RUNTIME / 'credentials.json').read_text(encoding='utf-8-sig'))
        configuration = LOCAL / ('mysql-smoke-root-' + uuid.uuid4().hex + '.cnf')
        configuration.write_text('[client]\nhost=127.0.0.1\nport=3306\nuser=root\npassword='
                                 + secrets['mysqlRootPassword'] + '\ndefault-character-set=utf8mb4\n', encoding='utf-8')
    try:
        return subprocess.check_output([str(executable), f'--defaults-extra-file={configuration}',
                                       '-N', '-B', '--database=sky_take_out'],
                                      input=statement + '\n', text=True, encoding='utf-8').strip()
    finally:
        if administrative:
            configuration.unlink(missing_ok=True)

def receive_exact(connection, count):
    content = b''
    while len(content) < count:
        chunk = connection.recv(count - len(content))
        if not chunk:
            raise AssertionError('WebSocket connection closed early')
        content += chunk
    return content

def receive_event(connection):
    message = b''
    frames = []
    first = True
    while True:
        header = receive_exact(connection, 2)
        frames.append(header.hex())
        length = header[1] & 127
        if length == 126:
            length = struct.unpack('!H', receive_exact(connection, 2))[0]
        elif length == 127:
            length = struct.unpack('!Q', receive_exact(connection, 8))[0]
        assert not header[1] & 128, 'Server frames must not be masked'
        opcode = header[0] & 15
        assert opcode == (1 if first else 0), 'Expected a WebSocket text/continuation frame'
        message += receive_exact(connection, length)
        first = False
        if header[0] & 128:
            assert message, 'Empty WebSocket message, frame headers: ' + str(frames)
            return json.loads(message.decode('utf-8'))

def submit(label, address, dish, marker):
    api(label + ': add dish', 'POST', '/user/shoppingCart/add', {'dishId': dish['id']}, USER)
    cart = api(label + ': cart', 'GET', '/user/shoppingCart/list', headers=USER)
    amount = sum(float(item['amount']) * item['number'] for item in cart)
    eta = dt.datetime.now(dt.timezone(dt.timedelta(hours=8))) + dt.timedelta(hours=1)
    payload = {'requestKey': uuid.uuid4().hex, 'addressBookId': address, 'payMethod': 1, 'amount': amount, 'remark': marker,
               'estimatedDeliveryTime': eta.strftime('%Y-%m-%d %H:%M:%S'), 'deliveryStatus': 1,
               'tablewareNumber': 1, 'tablewareStatus': 1, 'packAmount': 0}
    return payload

def main():
    marker = 'smoke-' + uuid.uuid4().hex[:12]
    page_status, page_content = raw('GET', '/')
    assert page_status == 200 and b'id=app' in page_content
    page_text = page_content.decode('utf-8')
    for source in re.findall(r'<script[^>]+src=["\']?([^"\' >]+)', page_text):
        asset_status, asset = raw('GET', '/' + source.lstrip('./'))
        assert asset_status == 200 and not asset.startswith(b'<!DOCTYPE html>')
    RESULTS.append({'check': 'built frontend HTML and script assets served', 'passed': True})
    api('admin rejects missing token', 'GET', '/api/employee/page?page=1&pageSize=10', status=401)
    api('user rejects missing token', 'GET', '/user/shoppingCart/list', status=401)
    admin = api('admin login', 'POST', '/api/employee/login', {'username': 'admin', 'password': '123456'})
    ADMIN['token'] = admin['token']
    api('employee page', 'GET', '/api/employee/page?page=1&pageSize=10', headers=ADMIN)
    api('category page', 'GET', '/api/category/page?page=1&pageSize=10', headers=ADMIN)
    page = api('dish page', 'GET', '/api/dish/page?page=1&pageSize=100', headers=ADMIN)
    dish = next(item for item in page['records'] if item['status'] == 1)
    api('setmeal page', 'GET', '/api/setmeal/page?page=1&pageSize=10', headers=ADMIN)
    api('shop status available', 'GET', '/user/shop/status')
    api('open shop', 'PUT', '/api/shop/1', headers=ADMIN)
    assert api('shop is open', 'GET', '/user/shop/status') == 1
    api('local login rejects unknown code', 'POST', '/user/user/login', {'code': 'invalid-code'}, code=0)
    user = api('local user login', 'POST', '/user/user/login', {'code': 'local-demo'})
    USER['authentication'] = user['token']
    other = api('second local user login', 'POST', '/user/user/login', {'code': 'local-second'})
    api('user categories', 'GET', '/user/category/list?type=1', headers=USER)
    api('user dishes and Redis cache', 'GET', '/user/dish/list?categoryId=' + str(dish['categoryId']), headers=USER)
    api('cached dish response', 'GET', '/user/dish/list?categoryId=' + str(dish['categoryId']), headers=USER)
    api('create local address', 'POST', '/user/addressBook', {'consignee': marker, 'phone': '13800000000',
        'sex': '1', 'provinceName': '湖北省', 'cityName': '武汉市', 'districtName': '洪山区',
        'detail': '本地演示地址', 'label': '演示', 'isDefault': 0}, USER)
    addresses = api('address list', 'GET', '/user/addressBook/list', headers=USER)
    address = next(item['id'] for item in addresses if item['consignee'] == marker)
    api('clear demo cart', 'DELETE', '/user/shoppingCart/clean', headers=USER)
    payload = submit('completed order', address, dish, marker)
    order = api('submit order', 'POST', '/user/order/submit', payload, USER)
    assert api('cart is cleared after order', 'GET', '/user/shoppingCart/list', headers=USER) == []
    assert int(sql(f'SELECT COUNT(*) FROM order_detail WHERE order_id={int(order["id"])}')) == 1
    api('other user cannot simulate this payment', 'POST', '/user/local/orders/' + order['orderNumber'] + '/pay',
        headers={'authentication': other['token']}, code=0)
    prepay = api('simulated prepayment', 'PUT', '/user/order/payment',
                 {'orderNumber': order['orderNumber'], 'payMethod': 1}, USER)
    assert prepay['signType'] == 'LOCAL_SIMULATION'
    with socket.create_connection(('127.0.0.1', 8081), timeout=8) as connection:
        connection.settimeout(8)
        key = base64.b64encode(os.urandom(16)).decode()
        handshake = (f'GET /ws/{marker} HTTP/1.1\r\nHost: 127.0.0.1:8081\r\n'
                     f'Upgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Key: {key}\r\n'
                     'Sec-WebSocket-Version: 13\r\n' + 'Sec-WebSocket-Protocol: sky-admin, auth.' + ADMIN['token'] + '\r\n\r\n')
        connection.sendall(handshake.encode())
        header = b''
        while b'\r\n\r\n' not in header:
            header += receive_exact(connection, 1)
        assert header.split(b'\r\n', 1)[0].split()[1] == b'101'
        assert receive_event(connection)['type'] == 0, 'WebSocket endpoint must acknowledge registration'
        api('simulate successful payment', 'POST', '/user/local/orders/' + order['orderNumber'] + '/pay', headers=USER)
        event = receive_event(connection)
        assert event['type'] == 1 and int(event['orderId']) == int(order['id'])
        RESULTS.append({'check': 'WebSocket new-order event through Nginx', 'passed': True})
        api('user reminder', 'GET', '/user/order/reminder/' + str(order['id']), headers=USER)
        assert receive_event(connection)['type'] == 2
        RESULTS.append({'check': 'WebSocket reminder event through Nginx', 'passed': True})
        connection.sendall(b'\x88\x80' + os.urandom(4))
    api('duplicate local payment is rejected', 'POST', '/user/local/orders/' + order['orderNumber'] + '/pay', headers=USER, code=0)
    api('cannot complete before delivery', 'PUT', '/api/order/complete/' + str(order['id']), headers=ADMIN, code=0)
    api('merchant accepts order', 'PUT', '/api/order/confirm', {'id': order['id']}, ADMIN)
    api('merchant starts delivery', 'PUT', '/api/order/delivery/' + str(order['id']), headers=ADMIN)
    api('merchant completes order', 'PUT', '/api/order/complete/' + str(order['id']), headers=ADMIN)
    detail = api('completed order persisted', 'GET', '/api/order/details/' + str(order['id']), headers=ADMIN)
    assert detail['status'] == 5 and detail['deliveryTime']
    api('completed order cannot complete again', 'PUT', '/api/order/complete/' + str(order['id']), headers=ADMIN, code=0)
    api('order history', 'GET', '/user/order/historyOrders?pageNum=1&pageSize=10', headers=USER)
    api('order search', 'GET', '/api/order/conditionSearch?page=1&pageSize=10', headers=ADMIN)
    api('order statistics', 'GET', '/api/order/statistics', headers=ADMIN)
    for endpoint in ('businessData', 'overviewOrders', 'overviewDishes', 'overviewSetmeals'):
        api('workspace ' + endpoint, 'GET', '/api/workspace/' + endpoint, headers=ADMIN)
    date = dt.datetime.now(dt.timezone(dt.timedelta(hours=8))).date().isoformat()
    api('order search with date filters', 'GET', f'/api/order/conditionSearch?page=1&pageSize=10&beginTime={date}%2000:00:00&endTime={date}%2023:59:59', headers=ADMIN)
    for endpoint in ('turnoverStatistics', 'userStatistics', 'ordersStatistics', 'top10'):
        api(endpoint, 'GET', f'/api/report/{endpoint}?begin={date}&end={date}', headers=ADMIN)
    payload = submit('unpaid cancellation', address, dish, marker + '-cancel')
    unpaid = api('submit unpaid order', 'POST', '/user/order/submit', payload, USER)
    api('cancel unpaid order', 'PUT', '/user/order/cancel/' + str(unpaid['id']), headers=USER)
    assert sql(f'SELECT status FROM orders WHERE id={int(unpaid["id"])}') == '6'
    payload = submit('paid refund', address, dish, marker + '-refund')
    paid = api('submit refund order', 'POST', '/user/order/submit', payload, USER)
    api('simulate payment for refund', 'POST', '/user/local/orders/' + paid['orderNumber'] + '/pay', headers=USER)
    api('cancel paid order and simulate refund', 'PUT', '/user/order/cancel/' + str(paid['id']), headers=USER)
    refund_deadline = time.monotonic() + 12
    while sql(f'SELECT status,pay_status FROM orders WHERE id={int(paid["id"])}') != '6\t2':
        assert time.monotonic() < refund_deadline, 'Local refund task did not complete'
        time.sleep(0.2)
    payload = submit('transaction rollback', address, dish, marker + '-rollback')
    trigger = 'sky_local_smoke_' + uuid.uuid4().hex[:12]
    try:
        sql(f"DELIMITER //\nCREATE TRIGGER {trigger} BEFORE INSERT ON order_detail FOR EACH ROW BEGIN "
            f"IF (SELECT remark FROM orders WHERE id=NEW.order_id)='{marker}-rollback' THEN "
            "SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='Local smoke rollback probe'; END IF; END//\nDELIMITER ;", administrative=True)
        api('detail failure returns error', 'POST', '/user/order/submit', payload, USER, status=500)
        assert sql(f"SELECT COUNT(*) FROM orders WHERE remark='{marker}-rollback'") == '0'
        assert len(api('rollback preserves shopping cart', 'GET', '/user/shoppingCart/list', headers=USER)) == 1
        RESULTS.append({'check': 'failed detail insert rolls back order header', 'passed': True})
    finally:
        sql('DROP TRIGGER IF EXISTS ' + trigger, administrative=True)
    api('clean rollback cart', 'DELETE', '/user/shoppingCart/clean', headers=USER)
    png = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+j+ZkAAAAASUVORK5CYII=')
    boundary = 'SkyDemo' + uuid.uuid4().hex
    multipart = (f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="probe.png"\r\n'
                 'Content-Type: image/png\r\n\r\n').encode() + png + f'\r\n--{boundary}--\r\n'.encode()
    upload_status, uploaded = raw('POST', '/api/common/upload', multipart,
                                  dict(ADMIN, **{'Content-Type': 'multipart/form-data; boundary=' + boundary}))
    upload = json.loads(uploaded)
    assert upload_status == 200 and upload['code'] == 1
    image_status, actual_png = raw('GET', upload['data'])
    assert image_status == 200 and actual_png == png
    RESULTS.append({'check': 'local upload and Nginx file serving', 'passed': True})
    export_status, export = raw('GET', '/api/report/export', headers=ADMIN)
    assert export_status == 200 and export.startswith(b'PK')
    export_path = LOCAL / 'smoke-export.xlsx'
    export_path.write_bytes(export)
    with zipfile.ZipFile(export_path) as archive:
        assert 'xl/workbook.xml' in archive.namelist()
    RESULTS.append({'check': 'Excel report export', 'passed': True})
    RESULTS.append({'check': '14 business tables and Flyway history', 'passed': sql("SELECT COUNT(*) FROM information_schema.tables WHERE table_schema='sky_take_out'") == '15'})
    assert RESULTS[-1]['passed']
    print(f'PASS {len(RESULTS)} checks; completed demo order {order["orderNumber"]}', flush=True)
    return order

if __name__ == '__main__':
    LOCAL.mkdir(exist_ok=True)
    report = {'profile': 'local', 'external_services': 'simulated', 'checks': RESULTS}
    try:
        report['demo_order'] = main()
        report['passed'] = True
    except Exception as error:
        report['passed'] = False
        report['failure'] = str(error)
        raise
    finally:
        report['executed_at'] = dt.datetime.now(dt.timezone(dt.timedelta(hours=8))).isoformat()
        (LOCAL / 'smoke-report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
