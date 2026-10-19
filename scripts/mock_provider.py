"""Loopback-only mock merchant, separate from the app. SQLite commits precede fault responses.
No real funds. One refund_number = one actual ledger refund, but arbitrarily many HTTP calls.
"""
import argparse
import json
import sqlite3
import time
from decimal import Decimal, InvalidOperation
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


def connect(path):
    db = sqlite3.connect(path, timeout=10)
    db.row_factory = sqlite3.Row
    return db


def initialize(path):
    with connect(path) as db:
        db.executescript('''
          PRAGMA journal_mode=WAL; PRAGMA synchronous=FULL;
          CREATE TABLE IF NOT EXISTS payments(number TEXT PRIMARY KEY, cents INTEGER NOT NULL);
          CREATE TABLE IF NOT EXISTS refunds(refund_number TEXT PRIMARY KEY, number TEXT NOT NULL, cents INTEGER NOT NULL);
          CREATE TABLE IF NOT EXISTS calls(id INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT, number TEXT, refund_number TEXT, created REAL);
          CREATE TABLE IF NOT EXISTS faults(refund_number TEXT PRIMARY KEY, delay_ms INTEGER, response TEXT, remaining INTEGER);
          CREATE TABLE IF NOT EXISTS fault_plan(refund_number TEXT,kind TEXT,delay_ms INTEGER,response TEXT,
            remaining INTEGER,record_refund INTEGER,http_status INTEGER,PRIMARY KEY(refund_number,kind));
        ''')


def cents(value):
    amount = Decimal(str(value)) * 100
    if not amount.is_finite() or amount <= 0 or amount != amount.to_integral_value():
        raise ValueError('invalid cents')
    return int(amount)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def reply(self, code, result):
        body = json.dumps(result).encode('utf-8')
        try:
            self.send_response(code)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass  # An uncertain caller outcome must not roll back the committed merchant ledger.

    def do_GET(self):
        if self.path == '/health':
            self.reply(200, {'simulation': True})
        elif self.path == '/ledger':
            with connect(self.server.ledger) as db:
                result = {table: [dict(row) for row in db.execute('SELECT * FROM ' + table)]
                          for table in ('payments', 'refunds', 'calls')}
            self.reply(200, result)
        else:
            self.reply(404, {})

    def do_POST(self):
        delay, response, http_status = 0, 'SUCCESS', 200
        try:
            length = int(self.headers.get('Content-Length', '0'))
            if not 0 < length <= 4096:
                raise ValueError('invalid body')
            value = json.loads(self.rfile.read(length))
            with connect(self.server.ledger) as db:
                db.execute('BEGIN IMMEDIATE')  # Serialize duplicate refunds across concurrent requests.
                if self.path == '/fault':
                    delay = int(value.get('delayMs', 0))
                    response = value.get('response', 'SUCCESS')
                    kind, count = value.get('kind', 'refund'), int(value.get('repeat', 1))
                    http_status = int(value.get('httpStatus', 200))
                    record = bool(value.get('recordRefund', True))
                    if (not 0 <= delay <= 30000 or response not in ('SUCCESS', 'FAILED', 'PROCESSING', 'CLOSED', 'ABNORMAL', 'NOT_FOUND', 'INVALID_JSON')
                            or kind not in ('refund', 'query') or not 1 <= count <= 20 or http_status not in (200, 503)):
                        raise ValueError('invalid fault')
                    db.execute('INSERT OR REPLACE INTO fault_plan VALUES(?,?,?,?,?,?,?)',
                               (value['refundNumber'], kind, delay, response, count, int(record), http_status))
                    delay, response, http_status = 0, 'SUCCESS', 200
                elif self.path == '/pay':
                    number, total = value['number'], cents(value['total'])
                    old = db.execute('SELECT cents FROM payments WHERE number=?', (number,)).fetchone()
                    if old and old['cents'] != total:
                        raise ValueError('payment conflict')
                    db.execute('INSERT OR IGNORE INTO payments VALUES(?,?)', (number, total))
                    db.execute('INSERT INTO calls(kind,number,created) VALUES(?,?,?)', ('pay', number, time.time()))
                elif self.path == '/refund':
                    number, key = value['number'], value['refundNumber']
                    total, refund = cents(value['total']), cents(value['refund'])
                    paid = db.execute('SELECT cents FROM payments WHERE number=?', (number,)).fetchone()
                    if not paid or paid['cents'] != total or refund > total:
                        raise ValueError('refund has no matching payment')
                    old = db.execute('SELECT * FROM refunds WHERE refund_number=?', (key,)).fetchone()
                    if old and (old['number'] != number or old['cents'] != refund):
                        raise ValueError('refund number conflict')
                    if not old and db.execute('SELECT 1 FROM refunds WHERE number=?', (number,)).fetchone():
                        raise ValueError('over-refund')
                    fault = db.execute("SELECT * FROM fault_plan WHERE refund_number=? AND kind='refund' AND remaining>0", (key,)).fetchone()
                    if not fault or fault['record_refund']:
                        db.execute('INSERT OR IGNORE INTO refunds VALUES(?,?,?)', (key, number, refund))
                    db.execute('INSERT INTO calls(kind,number,refund_number,created) VALUES(?,?,?,?)',
                               ('refund', number, key, time.time()))
                    if fault:
                        delay, response, http_status = fault['delay_ms'], fault['response'], fault['http_status']
                        db.execute("UPDATE fault_plan SET remaining=remaining-1 WHERE refund_number=? AND kind='refund'", (key,))
                elif self.path == '/query':
                    key = value['refundNumber']
                    response = 'SUCCESS' if db.execute('SELECT 1 FROM refunds WHERE refund_number=?', (key,)).fetchone() else 'NOT_FOUND'
                    db.execute('INSERT INTO calls(kind,refund_number,created) VALUES(?,?,?)', ('query', key, time.time()))
                    fault = db.execute("SELECT * FROM fault_plan WHERE refund_number=? AND kind='query' AND remaining>0", (key,)).fetchone()
                    if fault:
                        delay, response, http_status = fault['delay_ms'], fault['response'], fault['http_status']
                        db.execute("UPDATE fault_plan SET remaining=remaining-1 WHERE refund_number=? AND kind='query'", (key,))
                else:
                    self.reply(404, {})
                    return
            # Transaction has COMMITTED. Delayed/failed HTTP confirmation changes no ledger row.
            time.sleep(delay / 1000)
            self.reply(http_status, 'malformed-provider-body' if response == 'INVALID_JSON' else {'status': response, 'simulation': True})
        except (ValueError, KeyError, InvalidOperation, json.JSONDecodeError) as error:
            self.reply(409, {'error': type(error).__name__, 'simulation': True})
        except sqlite3.Error:
            self.reply(503, {'error': 'ledger unavailable'})


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', type=int, required=True)
    parser.add_argument('--ledger', type=Path, required=True)
    args = parser.parse_args()
    args.ledger.parent.mkdir(parents=True, exist_ok=True)
    initialize(args.ledger)
    server = ThreadingHTTPServer(('127.0.0.1', args.port), Handler)
    server.daemon_threads = True
    server.ledger = args.ledger
    server.serve_forever()


if __name__ == '__main__':
    main()
