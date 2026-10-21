import asyncio
import json
import uuid
from pathlib import Path

import aiosqlite

SCHEMA = '''
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;
PRAGMA busy_timeout=5000;
CREATE TABLE IF NOT EXISTS courses (
 id INTEGER PRIMARY KEY, name TEXT NOT NULL, description TEXT NOT NULL,
 price INTEGER NOT NULL CHECK(price>0), channel_chat_id INTEGER NOT NULL,
 active INTEGER NOT NULL DEFAULT 1, deleted INTEGER NOT NULL DEFAULT 0,
 created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS orders (
 id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL, course_id INTEGER NOT NULL REFERENCES courses(id),
 amount INTEGER NOT NULL, channel_chat_id INTEGER NOT NULL, title TEXT NOT NULL,
 bill_id TEXT UNIQUE, payment_url TEXT, status TEXT NOT NULL DEFAULT 'creating',
 external_reference TEXT NOT NULL UNIQUE, payment_id TEXT UNIQUE,
 created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, paid_at TEXT, last_checked TEXT);
CREATE UNIQUE INDEX IF NOT EXISTS one_open_order ON orders(user_id,course_id)
 WHERE status IN ('creating','pending','paid','review');
CREATE INDEX IF NOT EXISTS orders_poll ON orders(status,last_checked);
CREATE TABLE IF NOT EXISTS purchases (
 id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL, course_id INTEGER NOT NULL REFERENCES courses(id),
 order_id INTEGER NOT NULL UNIQUE REFERENCES orders(id), invite_link TEXT UNIQUE,
 status TEXT NOT NULL DEFAULT 'waiting', expires_at INTEGER, notified INTEGER NOT NULL DEFAULT 0,
 created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, UNIQUE(user_id,course_id));
CREATE TABLE IF NOT EXISTS admin_logs (
 id INTEGER PRIMARY KEY, admin_id INTEGER NOT NULL, action TEXT NOT NULL, details TEXT NOT NULL,
 created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
CREATE INDEX IF NOT EXISTS admin_logs_time ON admin_logs(created_at);
'''


class Database:
    def __init__(self, path):
        self.path = path
        self.lock = asyncio.Lock()

    async def open(self):
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = await aiosqlite.connect(self.path, isolation_level=None)
        self.conn.row_factory = aiosqlite.Row
        await self.conn.executescript(SCHEMA)

    async def close(self):
        await self.conn.close()

    async def rows(self, sql, args=()):
        async with self.lock:
            async with self.conn.execute(sql, args) as cur:
                return [dict(x) for x in await cur.fetchall()]

    async def one(self, sql, args=()):
        rows = await self.rows(sql, args)
        return rows[0] if rows else None

    async def execute(self, sql, args=()):
        async with self.lock:
            cur = await self.conn.execute(sql, args)
            return cur.lastrowid

    async def audit(self, admin, action, details):
        await self.execute('INSERT INTO admin_logs(admin_id,action,details) VALUES(?,?,?)',
                           (admin, action, json.dumps(details, ensure_ascii=False)))

    async def course_change(self, admin, action, sql, args):
        async with self.lock:
            await self.conn.execute('BEGIN IMMEDIATE')
            try:
                cur = await self.conn.execute(sql, args)
                await self.conn.execute('INSERT INTO admin_logs(admin_id,action,details) VALUES(?,?,?)',
                                        (admin, action, json.dumps({'args': args, 'id': cur.lastrowid}, ensure_ascii=False)))
                await self.conn.commit()
                return cur.lastrowid
            except BaseException:
                await self.conn.rollback()
                raise

    async def order(self, user, course):
        async with self.lock:
            await self.conn.execute('BEGIN IMMEDIATE')
            try:
                cur = await self.conn.execute("SELECT * FROM orders WHERE user_id=? AND course_id=? AND status IN ('creating','pending','paid','review')", (user, course))
                row = await cur.fetchone()
                if not row:
                    cur = await self.conn.execute('SELECT * FROM courses WHERE id=? AND active=1 AND deleted=0', (course,))
                    c = await cur.fetchone()
                    if not c:
                        raise ValueError('Kurs hozir sotuvda emas.')
                    ref = 'ORD-' + uuid.uuid4().hex
                    cur = await self.conn.execute('INSERT INTO orders(user_id,course_id,amount,channel_chat_id,title,external_reference) VALUES(?,?,?,?,?,?)',
                        (user, course, c['price'], c['channel_chat_id'], c['name'], ref))
                    cur = await self.conn.execute('SELECT * FROM orders WHERE id=?', (cur.lastrowid,))
                    row = await cur.fetchone()
                await self.conn.commit()
                return dict(row)
            except BaseException:
                await self.conn.rollback()
                raise

    async def mark_paid(self, order, payment_id):
        async with self.lock:
            await self.conn.execute('BEGIN IMMEDIATE')
            try:
                await self.conn.execute("UPDATE orders SET status='paid',payment_id=?,paid_at=COALESCE(paid_at,CURRENT_TIMESTAMP) WHERE id=?", (payment_id, order['id']))
                await self.conn.execute('INSERT OR IGNORE INTO purchases(user_id,course_id,order_id) VALUES(?,?,?)',
                                        (order['user_id'], order['course_id'], order['id']))
                await self.conn.commit()
            except BaseException:
                await self.conn.rollback()
                raise
