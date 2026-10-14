"""Owned-process harness: unique MySQL schema/user, isolated Redis, independent SQLite merchant.
Never attach to, terminate, migrate or write the normal sky_take_out application/database.
Only Popen handles created here can be stopped. Test artifacts/credentials stay under .local.
"""
import concurrent.futures
import json
import os
import platform
import secrets
import socket
import subprocess
import sys
import threading
import time
import uuid
from datetime import datetime, timedelta
from pathlib import Path

import pymysql
import requests
from pymysql.constants import CLIENT

ROOT = Path(__file__).resolve().parent.parent
RUNTIME = Path(os.environ.get('SKY_RUNTIME_DIR', ROOT.parent / '.runtime'))


def port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


def wait(condition, timeout=30, description='condition'):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = condition()
        if value:
            return value
        time.sleep(.08)
    raise AssertionError('Timed out waiting for ' + description)


def listening(value):
    try:
        with socket.create_connection(('127.0.0.1', value), timeout=.3):
            return True
    except OSError:
        return False


class Lab:
    def __init__(self):
        self.run = uuid.uuid4().hex[:10]
        self.directory = ROOT / '.local' / 'reliability' / self.run
        self.directory.mkdir(parents=True)
        self.schema = 'campus_rel_' + self.run
        self.db_user = 'cr_' + self.run
        self.password = secrets.token_hex(16)
        self.redis_password = secrets.token_hex(16)
        self.tools = {name: next(RUNTIME.glob(pattern)) for name, pattern in
                      [('java', 'jdk1.8*'), ('mysql', 'mysql-*-winx64')]}
        self.redis_home = next((RUNTIME / 'redis').glob('*'))
        self.credentials = json.loads((RUNTIME / 'credentials.json').read_text(encoding='utf-8-sig'))
        self.owned = []
        self.apps = {}
        self.admin = {}
        self.ports = {name: port() for name in ('a', 'b', 'redis', 'provider')}
        self.provider = 'http://127.0.0.1:' + str(self.ports['provider'])
        self.pool = 10
        self.serial = 0
        self.actor_serial = 0
        self.checks = []
        self.http = threading.local()
        self.http_sessions = []
        self.environment = dict(os.environ,
                                SKY_DB_PASSWORD=self.password, SKY_REDIS_PASSWORD=self.redis_password,
                                SKY_ADMIN_JWT_SECRET=secrets.token_hex(16), SKY_USER_JWT_SECRET=secrets.token_hex(16),
                                SKY_UPLOAD_DIR=str(self.directory / 'uploads'))

    def spawn(self, name, args, env=None, cwd=None):
        self.serial += 1
        log = (self.directory / (name + '-' + str(self.serial) + '.log')).open('wb')
        flags = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
        proc = subprocess.Popen([str(arg) for arg in args], cwd=cwd or ROOT, env=env or self.environment,
                                stdout=log, stderr=subprocess.STDOUT, creationflags=flags)
        log.close()
        self.owned.append(proc)
        return proc

    def stop(self, proc):
        assert any(proc is item for item in self.owned), 'Refusing to stop an unowned process'
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=15)

    def db(self, root=False):
        return pymysql.connect(host='127.0.0.1', port=3306, user='root' if root else self.db_user,
                               password=self.credentials['mysqlRootPassword'] if root else self.password,
                               database=None if root else self.schema, autocommit=True, charset='utf8mb4',
                               client_flag=CLIENT.MULTI_STATEMENTS, connect_timeout=5, read_timeout=15)

    def sql(self, query, args=(), root=False):
        with self.db(root) as db, db.cursor(pymysql.cursors.DictCursor) as cursor:
            cursor.execute(query, args)
            return cursor.fetchall()

    def one(self, query, args=()):
        rows = self.sql(query, args)
        return rows[0] if rows else None

    def scalar(self, query, args=(), root=False):
        return next(iter(self.sql(query, args, root=root)[0].values()))

    def setup(self):
        jar = ROOT / 'sky-take-out/sky-server/target/sky-server-1.0-SNAPSHOT.jar'
        assert jar.exists(), 'Build backend with scripts/build.ps1 first'
        if not listening(3306):
            self.mysql = self.spawn('mysql', [self.tools['mysql'] / 'bin/mysqld.exe', '--no-defaults',
                                    '--basedir=' + str(self.tools['mysql']), '--datadir=' + str(RUNTIME / 'mysql-data'),
                                    '--bind-address=127.0.0.1', '--port=3306', '--mysqlx=0', '--console'])
            wait(lambda: listening(3306), description='MySQL')
        # Never modify or import into sky_take_out. The identifier comes exclusively from uuid.hex.
        assert self.schema.startswith('campus_rel_') and self.schema[11:].isalnum()
        with self.db(True) as db, db.cursor() as cursor:
            cursor.execute('CREATE DATABASE `' + self.schema + '` CHARACTER SET utf8mb4')
            cursor.execute('CREATE USER %s@%s IDENTIFIED BY %s', (self.db_user, '127.0.0.1', self.password))
            cursor.execute('GRANT ALL PRIVILEGES ON `' + self.schema + '`.* TO %s@%s', (self.db_user, '127.0.0.1'))
            cursor.execute('USE `' + self.schema + '`')
            initial = (ROOT / 'scripts/sql/000-initial-schema.sql').read_text(encoding='utf-8')
            statements = initial.split(';', 2)[2]  # Drop only the source's CREATE DATABASE + USE statements.
            assert 'USE `sky_take_out`' not in statements and 'CREATE DATABASE' not in statements
            cursor.execute(statements)
            while cursor.nextset():
                pass
            cursor.execute("UPDATE employee SET password=MD5('123456') WHERE username='admin'")
        config = self.directory / 'redis.conf'
        cygwin_path = lambda path: '/cygdrive/' + str(path)[0].lower() + '/' + str(path)[3:].replace('\\', '/')
        config.write_text('bind 127.0.0.1\nport ' + str(self.ports['redis']) + '\nrequirepass ' + self.redis_password
                          + '\nsave ""\nappendonly no\ndir "' + cygwin_path(self.directory) + '"\n', encoding='utf-8')
        self.redis = self.spawn('redis', [self.redis_home / 'redis-server.exe', cygwin_path(config)], cwd=self.redis_home)
        wait(lambda: listening(self.ports['redis']), description='isolated Redis')
        self.start_provider()
        self.start_app('a')
        self.start_app('b')
        self.call('a', 'PUT', '/admin/shop/1', admin=True)
        self.dish = self.one('SELECT * FROM dish WHERE status=1 AND price>0 ORDER BY id LIMIT 1')
        assert self.dish
        self.sql('CREATE TABLE reliability_probe(id INT PRIMARY KEY,value INT NOT NULL) ENGINE=InnoDB')
        for index in range(1, 21):
            self.sql('INSERT INTO reliability_probe VALUES(%s,0)', (index,))
        (self.directory / 'configuration.json').write_text(json.dumps({
            'schema': self.schema, 'dbUser': self.db_user, 'dbPassword': self.password, 'ports': self.ports
        }), encoding='utf-8')
        print('Isolated lab ready: two apps, separate merchant ledger and Redis', flush=True)

    def start_provider(self):
        self.merchant = self.spawn('provider', [sys.executable, ROOT / 'scripts/mock_provider.py',
                                    '--port', self.ports['provider'], '--ledger', self.directory / 'merchant.sqlite'])
        wait(lambda: listening(self.ports['provider']), description='mock merchant')

    def start_app(self, name):
        args = [self.tools['java'] / 'bin/java.exe', '-Xms128m', '-Xmx384m', '-Dfile.encoding=UTF-8', '-jar',
                ROOT / 'sky-take-out/sky-server/target/sky-server-1.0-SNAPSHOT.jar', '--spring.profiles.active=local',
                '--server.port=' + str(self.ports[name]), '--sky.datasource.database=' + self.schema,
                '--sky.datasource.username=' + self.db_user, '--sky.redis.port=' + str(self.ports['redis']),
                '--sky.redis.database=0', '--sky.local.reliability.enabled=true', '--sky.refund.scan-enabled=false',
                '--sky.refund.lease-seconds=2', '--sky.order.timeout-scan-ms=3600000',
                '--sky.local.provider.url=' + self.provider, '--sky.local.provider.read-timeout-ms=6000',
                '--spring.datasource.druid.max-active=' + str(self.pool), '--spring.datasource.druid.max-wait=3000',
                '--spring.datasource.druid.url=jdbc:mysql://127.0.0.1:3306/' + self.schema
                + '?serverTimezone=Asia/Shanghai&useUnicode=true&characterEncoding=utf-8&useSSL=false'
                + '&allowPublicKeyRetrieval=true&sessionVariables=innodb_lock_wait_timeout=1',
                '--debug=false', '--logging.level.root=warn', '--logging.level.com.sky=warn',
                '--logging.level.com.sky.mapper=warn', '--logging.level.com.sky.service=warn',
                '--logging.level.com.sky.controller=warn', '--logging.level.org.springframework=warn']
        proc = self.spawn('app-' + name, args)
        self.apps[name] = proc
        def ready():
            if proc.poll() is not None:
                raise AssertionError('App ' + name + ' exited; inspect private .local logs')
            try:
                return requests.get(self.base(name) + '/user/shop/status', timeout=1).status_code == 200
            except requests.RequestException:
                return False
        wait(ready, timeout=60, description='app ' + name)
        result = self.call(name, 'POST', '/admin/employee/login', {'username': 'admin', 'password': '123456'})
        self.admin[name] = {'token': result['data']['token']}
        config = self.call(name, 'GET', '/admin/local/reliability/configuration', admin=True)['data']
        assert config['databaseName'] == self.schema and int(config['lockWaitSeconds']) == 1, config

    def restart_app(self, name):
        self.stop(self.apps[name])
        self.start_app(name)

    def base(self, name):
        return 'http://127.0.0.1:' + str(self.ports[name])

    def call(self, name, method, path, body=None, actor=None, admin=False, expected=1):
        # One client pool per worker. Opening/closing a TCP socket for every call can exhaust
        # Windows ephemeral ports and turns a service benchmark into a client-resource test.
        if not hasattr(self.http, 'session'):
            self.http.session = requests.Session()
            self.http_sessions.append(self.http.session)
        response = self.http.session.request(method, self.base(name) + path, json=body,
                                             headers=self.admin[name] if admin else (actor['auth'] if actor else {}), timeout=20)
        try:
            result = response.json()
        except ValueError:
            result = {'code': None}
        result['_http'] = response.status_code
        if expected is not None:
            assert response.status_code == 200 and result.get('code') == expected, (method, path, response.status_code, result.get('msg'))
        return result

    def actor(self):
        self.actor_serial += 1
        code = 'local-test-' + self.run + '-' + str(self.actor_serial)
        user = self.call('a', 'POST', '/user/user/login', {'code': code})['data']
        return {'id': user['id'], 'auth': {'authentication': user['token']}}

    def meal(self, capacity=100):
        tomorrow = datetime.now().date() + timedelta(days=1)
        body = {'title': 'reliability-' + self.run, 'mealDate': str(tomorrow), 'capacity': capacity,
                'cutoffTime': str(tomorrow) + ' 10:30:00', 'pickupStart': str(tomorrow) + ' 11:30:00',
                'pickupEnd': str(tomorrow) + ' 13:00:00'}
        return self.call('a', 'POST', '/admin/meal/sessions', body, admin=True)['data']

    def cart(self, actor, name='a'):
        self.call(name, 'POST', '/user/shoppingCart/add', {'dishId': self.dish['id']}, actor=actor)

    def payload(self, meal):
        return {'requestKey': uuid.uuid4().hex, 'mealSessionId': meal['id'], 'contactName': '测试同学',
                'contactPhone': '13800000000', 'remark': self.run, 'deliveryStatus': 0, 'tablewareStatus': 1}

    def submit(self, actor, body, name='a'):
        return self.call(name, 'POST', '/user/order/submit', body, actor=actor)['data']

    def pay(self, actor, order, name='a'):
        return self.call(name, 'POST', '/user/local/orders/' + order['orderNumber'] + '/callback',
                         {'paidAmount': order['orderAmount']}, actor=actor)

    def cancel(self, actor, order, name='a'):
        return self.call(name, 'PUT', '/user/order/cancel/' + str(order['id']), actor=actor)

    def refunded_order(self):
        actor, meal = self.actor(), self.meal()
        self.cart(actor)
        order = self.submit(actor, self.payload(meal))
        self.pay(actor, order)
        self.cancel(actor, order)
        # DATETIME(0) can round the enqueue timestamp into the next second.
        # Wait for real eligibility rather than rewriting next_retry_time or treating a skipped claim as work.
        wait(lambda: self.due(order), timeout=3, description='new refund intent becomes due')
        return actor, meal, order, self.task(order)

    def task(self, order):
        return self.one('SELECT * FROM refund_task WHERE order_id=%s', (order['id'],))

    def run_refund(self, task, name='a'):
        return self.call(name, 'POST', '/admin/local/reliability/refunds/' + str(task['id']) + '/run', admin=True)

    def due(self, order):
        return self.scalar("SELECT COUNT(*) FROM refund_task WHERE order_id=%s AND "
                           "((status='PENDING' AND next_retry_time<=NOW()) OR (status='PROCESSING' AND lease_until<NOW()))", (order['id'],))

    def ledger(self, order):
        data = requests.get(self.provider + '/ledger', timeout=5).json()
        key = 'R' + order['orderNumber']
        return {'refundCalls': sum(row['kind'] == 'refund' and row['refund_number'] == key for row in data['calls']),
                'queryCalls': sum(row['kind'] == 'query' and row['refund_number'] == key for row in data['calls']),
                'actualRefunds': sum(row['refund_number'] == key for row in data['refunds']),
                'actualPayments': sum(row['number'] == order['orderNumber'] for row in data['payments'])}

    def fault(self, order, delay_ms, response='SUCCESS'):
        result = requests.post(self.provider + '/fault', json={'refundNumber': 'R' + order['orderNumber'],
                               'delayMs': delay_ms, 'response': response}, timeout=5)
        assert result.status_code == 200

    def arm(self, name, stage, key):
        gate = uuid.uuid4().hex
        self.call(name, 'POST', '/admin/local/reliability/gates/' + gate, {'stage': stage, 'key': key}, admin=True)
        return gate

    def reached(self, name, gate):
        return self.call(name, 'GET', '/admin/local/reliability/gates/' + gate, admin=True)['data']['reached']

    def release(self, name, gate):
        self.call(name, 'POST', '/admin/local/reliability/gates/' + gate + '/release', admin=True)

    def metrics(self, name):
        return self.call(name, 'GET', '/admin/local/reliability/metrics', admin=True)['data']

    def check(self, name, condition=True, evidence=None):
        assert condition, name + (': ' + json.dumps(evidence, default=str) if evidence else '')
        self.checks.append({'check': name, 'passed': True, 'evidence': evidence})
        (self.directory / 'progress.json').write_text(json.dumps(self.checks, indent=2, default=str), encoding='utf-8')
        print('PASS ' + name, flush=True)

    def invariants(self):
        rows = self.sql('SELECT m.id,m.reserved_count,m.capacity,COALESCE(SUM(CASE WHEN o.status<>6 THEN o.reserved_quantity ELSE 0 END),0) AS expected '
                        'FROM meal_session m LEFT JOIN orders o ON o.meal_session_id=m.id GROUP BY m.id')
        assert all(0 <= row['reserved_count'] <= row['capacity'] and row['reserved_count'] == row['expected'] for row in rows)
        assert not self.scalar('SELECT COUNT(*) FROM (SELECT user_id,request_key,COUNT(*) c FROM orders WHERE request_key IS NOT NULL GROUP BY user_id,request_key HAVING c>1) x')
        assert not self.scalar("SELECT COUNT(*) FROM refund_task r JOIN orders o ON r.order_id=o.id WHERE r.status='SUCCEEDED' AND (o.status<>6 OR o.pay_status<>2 OR o.refund_status<>'SUCCEEDED')")

    def db_metrics(self):
        values = self.sql("SHOW GLOBAL STATUS WHERE Variable_name IN ('Innodb_row_lock_current_waits','Innodb_row_lock_time','Innodb_row_lock_waits')", root=True)
        result = {row['Variable_name']: int(row['Value']) for row in values}
        metrics = self.sql("SELECT NAME,COUNT,STATUS FROM information_schema.INNODB_METRICS WHERE NAME='lock_deadlocks'", root=True)
        for row in metrics:
            if row['STATUS'] == 'enabled':
                result[row['NAME']] = int(row['COUNT'])
        return result

    def waits(self):
        return self.sql('SELECT l.OBJECT_NAME AS objectName,l.INDEX_NAME AS indexName,COUNT(*) AS waits '
                        'FROM performance_schema.data_lock_waits w JOIN performance_schema.data_locks l '
                        'ON w.REQUESTING_ENGINE_LOCK_ID=l.ENGINE_LOCK_ID AND w.ENGINE=l.ENGINE '
                        'WHERE l.OBJECT_SCHEMA=%s GROUP BY l.OBJECT_NAME,l.INDEX_NAME', (self.schema,), root=True)

    def environment_info(self):
        return {'os': platform.platform(), 'cpu': platform.processor(), 'logicalCpus': os.cpu_count(),
                'mysql': self.sql('SELECT VERSION() version', root=True)[0]['version'],
                'java': 'Corretto 8u504', 'springBoot': '2.7.3', 'redis': '7.4.3',
                'instances': 2, 'merchant': 'independent process / SQLite WAL', 'database': 'unique test schema',
                'leaseSeconds': 2, 'providerReadTimeoutMs': 6000, 'dbLockWaitTimeoutSeconds': 1,
                'refundAutoScan': False, 'businessClockZone': 'Asia/Shanghai'}

    def close(self):
        for session in self.http_sessions:
            session.close()
        for proc in reversed(self.owned):
            if getattr(self, 'mysql', None) is proc:
                if proc.poll() is None:
                    try:
                        self.sql('SHUTDOWN', root=True)
                        proc.wait(timeout=15)
                    except Exception:
                        self.stop(proc)
            else:
                self.stop(proc)
