"""SQLite ma’lumotlar bazasi: sxema, tranzaksiyalar va so‘rovlar.

Barcha so‘rovlar parametrlar bilan bajariladi. Yozish amallari bitta qulf orqali
ketma-ket bajariladi, shuning uchun bir ulanishdagi tranzaksiyalar aralashib ketmaydi.
"""
from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import Iterable, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import aiosqlite

ORDER_STATUSES = ('new', 'pending', 'paid', 'expired', 'cancelled', 'review')
OPEN_ORDER_STATUSES = ('new', 'pending')
PURCHASE_STATUSES = ('pending_invite', 'sending', 'link_created', 'sent', 'send_failed', 'invite_failed')
UNDELIVERED_STATUSES = ('pending_invite', 'link_created', 'send_failed', 'invite_failed')
USER_FILTERS = ('all', 'buyers', 'non_buyers', 'pending', 'inactive', 'blocked')
COURSE_FIELDS = ('name_uz', 'name_ru', 'description_uz', 'description_ru', 'price_uzs', 'channel_chat_id')

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id              INTEGER PRIMARY KEY,           -- Telegram user ID
    username        TEXT,
    first_name      TEXT,
    language        TEXT CHECK (language IN ('uz', 'ru')),
    is_blocked      INTEGER NOT NULL DEFAULT 0 CHECK (is_blocked IN (0, 1)),
    created_at      TEXT NOT NULL,
    last_active_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_users_last_active ON users(last_active_at);
CREATE INDEX IF NOT EXISTS idx_users_created ON users(created_at);
CREATE INDEX IF NOT EXISTS idx_users_username ON users(username COLLATE NOCASE);

CREATE TABLE IF NOT EXISTS courses (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    name_uz          TEXT NOT NULL DEFAULT '',
    name_ru          TEXT NOT NULL DEFAULT '',
    description_uz   TEXT NOT NULL DEFAULT '',
    description_ru   TEXT NOT NULL DEFAULT '',
    price_uzs        INTEGER NOT NULL CHECK (price_uzs > 0),
    channel_chat_id  INTEGER NOT NULL,
    is_active        INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1)),
    is_deleted       INTEGER NOT NULL DEFAULT 0 CHECK (is_deleted IN (0, 1)),
    created_at       TEXT NOT NULL,
    updated_at       TEXT NOT NULL,
    CHECK (name_uz <> '' OR name_ru <> '')
);
CREATE INDEX IF NOT EXISTS idx_courses_visible ON courses(is_deleted, is_active, id);

CREATE TABLE IF NOT EXISTS orders (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id              INTEGER NOT NULL REFERENCES users(id),
    course_id            INTEGER NOT NULL REFERENCES courses(id),
    amount_uzs           INTEGER NOT NULL CHECK (amount_uzs > 0),
    course_title         TEXT NOT NULL,
    external_reference   TEXT NOT NULL UNIQUE,
    idempotency_key      TEXT NOT NULL UNIQUE,
    tezcheck_bill_id     TEXT UNIQUE,
    payment_url          TEXT,
    tezcheck_payment_id  TEXT UNIQUE,
    payment_environment  TEXT,
    status               TEXT NOT NULL CHECK (status IN ('new','pending','paid','expired','cancelled','review')),
    last_error           TEXT,
    last_checked_at      TEXT,
    created_at           TEXT NOT NULL,
    updated_at           TEXT NOT NULL,
    paid_at              TEXT
);
-- Bir foydalanuvchi va bir kurs uchun faqat bitta ochiq buyurtma bo‘lishi mumkin.
CREATE UNIQUE INDEX IF NOT EXISTS ux_orders_open ON orders(user_id, course_id)
    WHERE status IN ('new', 'pending');
CREATE INDEX IF NOT EXISTS idx_orders_status ON orders(status, last_checked_at);
CREATE INDEX IF NOT EXISTS idx_orders_user ON orders(user_id, id);
CREATE INDEX IF NOT EXISTS idx_orders_course ON orders(course_id, id);

CREATE TABLE IF NOT EXISTS purchases (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id            INTEGER NOT NULL REFERENCES users(id),
    course_id          INTEGER NOT NULL REFERENCES courses(id),
    order_id           INTEGER NOT NULL UNIQUE REFERENCES orders(id),
    invite_link        TEXT,
    invite_expires_at  TEXT,
    invite_sent_at     TEXT,
    invite_count       INTEGER NOT NULL DEFAULT 0,
    status             TEXT NOT NULL CHECK (status IN
                        ('pending_invite','sending','link_created','sent','send_failed','invite_failed')),
    last_error         TEXT,
    attempts           INTEGER NOT NULL DEFAULT 0,
    next_retry_at      TEXT,
    created_at         TEXT NOT NULL,
    updated_at         TEXT NOT NULL,
    UNIQUE (user_id, course_id)
);
CREATE INDEX IF NOT EXISTS idx_purchases_status ON purchases(status, next_retry_at);

CREATE TABLE IF NOT EXISTS course_views (
    user_id          INTEGER NOT NULL REFERENCES users(id),
    course_id        INTEGER NOT NULL REFERENCES courses(id),
    views            INTEGER NOT NULL DEFAULT 1,
    first_viewed_at  TEXT NOT NULL,
    last_viewed_at   TEXT NOT NULL,
    PRIMARY KEY (user_id, course_id)
);

CREATE TABLE IF NOT EXISTS webhook_events (
    event_id      TEXT PRIMARY KEY,                -- Tezcheck tanasidagi "id"
    event_type    TEXT NOT NULL,
    delivery_id   TEXT,
    received_at   TEXT NOT NULL,
    status        TEXT NOT NULL CHECK (status IN ('received','processed','ignored','failed')),
    processed_at  TEXT,
    error         TEXT
);

CREATE TABLE IF NOT EXISTS admin_logs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    admin_id    INTEGER NOT NULL,
    action      TEXT NOT NULL,
    details     TEXT NOT NULL DEFAULT '',
    created_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_admin_logs_created ON admin_logs(created_at);
"""


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def ts(moment: datetime | None = None) -> str:
    """UTC vaqtni saralanadigan matn ko‘rinishida saqlaymiz."""
    return (moment or utcnow()).astimezone(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')


def parse_ts(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.strptime(value, '%Y-%m-%d %H:%M:%S').replace(tzinfo=timezone.utc)


def _like(text: str) -> str:
    return '%' + text.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_') + '%'


@dataclass(frozen=True)
class PaidResult:
    newly_paid: bool
    purchase_id: int | None
    duplicate: bool = False   # shu kurs boshqa buyurtma orqali allaqachon sotib olingan


class Database:
    def __init__(self, path: Path | str):
        self.path = Path(path)
        self.conn: aiosqlite.Connection | None = None
        self._lock = asyncio.Lock()

    # ------------------------------------------------------------------ ulanish
    async def connect(self) -> None:
        if str(self.path) != ':memory:':
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = await aiosqlite.connect(self.path, isolation_level=None)
        self.conn.row_factory = aiosqlite.Row
        await self.conn.execute('PRAGMA foreign_keys = ON')
        await self.conn.execute('PRAGMA busy_timeout = 5000')
        if str(self.path) != ':memory:':
            await self.conn.execute('PRAGMA journal_mode = WAL')
            await self.conn.execute('PRAGMA synchronous = NORMAL')
        await self.conn.executescript(SCHEMA)

    async def close(self) -> None:
        if self.conn is not None:
            await self.conn.close()
            self.conn = None

    @asynccontextmanager
    async def tx(self):
        """Atomar tranzaksiya: hammasi yoziladi yoki hech narsa yozilmaydi."""
        async with self._lock:
            await self.conn.execute('BEGIN IMMEDIATE')
            try:
                yield self.conn
            except BaseException:
                await self.conn.execute('ROLLBACK')
                raise
            await self.conn.execute('COMMIT')

    async def write(self, sql: str, params: Sequence[Any] = ()) -> aiosqlite.Cursor:
        async with self._lock:
            return await self.conn.execute(sql, params)

    async def one(self, sql: str, params: Sequence[Any] = ()) -> dict | None:
        async with self.conn.execute(sql, params) as cur:
            row = await cur.fetchone()
        return dict(row) if row else None

    async def all(self, sql: str, params: Sequence[Any] = ()) -> list[dict]:
        async with self.conn.execute(sql, params) as cur:
            return [dict(r) for r in await cur.fetchall()]

    async def value(self, sql: str, params: Sequence[Any] = ()) -> Any:
        async with self.conn.execute(sql, params) as cur:
            row = await cur.fetchone()
        return row[0] if row else None

    # ---------------------------------------------------------------- admin log
    @staticmethod
    async def _log(conn, admin_id: int, action: str, details: dict | str = '') -> None:
        text = details if isinstance(details, str) else json.dumps(details, ensure_ascii=False, default=str)
        await conn.execute('INSERT INTO admin_logs(admin_id, action, details, created_at) VALUES (?,?,?,?)',
                           (admin_id, action, text[:1000], ts()))

    async def log_admin(self, admin_id: int, action: str, details: dict | str = '') -> None:
        async with self.tx() as conn:
            await self._log(conn, admin_id, action, details)

    async def admin_logs(self, page: int, per_page: int = 15) -> tuple[list[dict], int]:
        rows = await self.all('SELECT * FROM admin_logs ORDER BY id DESC LIMIT ? OFFSET ?',
                              (per_page, page * per_page))
        return rows, await self.value('SELECT COUNT(*) FROM admin_logs')

    # -------------------------------------------------------------- foydalanuvchi
    async def touch_user(self, user_id: int, username: str | None, first_name: str | None) -> dict:
        """Foydalanuvchini yaratadi yoki oxirgi faolligini yangilaydi."""
        now = ts()
        await self.write(
            'INSERT INTO users(id, username, first_name, created_at, last_active_at) VALUES (?,?,?,?,?) '
            'ON CONFLICT(id) DO UPDATE SET username = excluded.username, '
            'first_name = excluded.first_name, last_active_at = excluded.last_active_at',
            (user_id, username, (first_name or '')[:128], now, now))
        return await self.get_user(user_id)

    async def get_user(self, user_id: int) -> dict | None:
        return await self.one('SELECT * FROM users WHERE id = ?', (user_id,))

    async def set_language(self, user_id: int, language: str) -> None:
        await self.write('UPDATE users SET language = ? WHERE id = ?', (language, user_id))

    async def set_blocked(self, admin_id: int, user_id: int, blocked: bool) -> bool:
        async with self.tx() as conn:
            cur = await conn.execute('UPDATE users SET is_blocked = ? WHERE id = ?', (int(blocked), user_id))
            if cur.rowcount:
                await self._log(conn, admin_id, 'user_block' if blocked else 'user_unblock', {'user_id': user_id})
            return bool(cur.rowcount)

    async def record_course_view(self, user_id: int, course_id: int) -> None:
        now = ts()
        await self.write(
            'INSERT INTO course_views(user_id, course_id, first_viewed_at, last_viewed_at) VALUES (?,?,?,?) '
            'ON CONFLICT(user_id, course_id) DO UPDATE SET views = views + 1, last_viewed_at = excluded.last_viewed_at',
            (user_id, course_id, now, now))

    async def users_summary(self) -> dict:
        now = utcnow()
        day = ts(now.replace(hour=0, minute=0, second=0, microsecond=0))
        d7, d30 = ts(now - timedelta(days=7)), ts(now - timedelta(days=30))
        return await self.one(
            'SELECT COUNT(*) AS total, '
            'SUM(last_active_at >= ?) AS active_today, SUM(last_active_at >= ?) AS active_7, '
            'SUM(last_active_at >= ?) AS active_30, SUM(created_at >= ?) AS new_today, '
            'SUM(created_at >= ?) AS new_7, SUM(is_blocked) AS blocked, '
            '(SELECT COUNT(DISTINCT user_id) FROM purchases) AS buyers FROM users',
            (day, d7, d30, day, d7))

    def _user_filter(self, flt: str) -> tuple[str, list]:
        if flt == 'buyers':
            return 'EXISTS (SELECT 1 FROM purchases p WHERE p.user_id = u.id)', []
        if flt == 'non_buyers':
            return 'NOT EXISTS (SELECT 1 FROM purchases p WHERE p.user_id = u.id)', []
        if flt == 'pending':
            return "EXISTS (SELECT 1 FROM orders o WHERE o.user_id = u.id AND o.status = 'pending')", []
        if flt == 'inactive':
            return 'u.last_active_at < ?', [ts(utcnow() - timedelta(days=30))]
        if flt == 'blocked':
            return 'u.is_blocked = 1', []
        return '1 = 1', []

    async def list_users(self, flt: str, sort: str, page: int, per_page: int = 10) -> tuple[list[dict], int]:
        where, params = self._user_filter(flt if flt in USER_FILTERS else 'all')
        order = 'u.last_active_at DESC' if sort == 'active' else 'u.created_at DESC'
        rows = await self.all(f'SELECT u.* FROM users u WHERE {where} ORDER BY {order}, u.id DESC LIMIT ? OFFSET ?',
                              (*params, per_page, page * per_page))
        total = await self.value(f'SELECT COUNT(*) FROM users u WHERE {where}', params)
        return rows, total

    async def search_users(self, query: str, limit: int = 20) -> list[dict]:
        query = query.strip().lstrip('@')
        if query.isdigit():
            return await self.all('SELECT * FROM users WHERE id = ?', (int(query),))
        pattern = _like(query)
        return await self.all(
            "SELECT * FROM users WHERE username LIKE ? ESCAPE '\\' OR first_name LIKE ? ESCAPE '\\' "
            'ORDER BY last_active_at DESC LIMIT ?', (pattern, pattern, limit))

    async def user_profile(self, user_id: int) -> dict | None:
        user = await self.get_user(user_id)
        if not user:
            return None
        user['views'] = await self.all(
            'SELECT v.*, c.name_uz, c.name_ru FROM course_views v JOIN courses c ON c.id = v.course_id '
            'WHERE v.user_id = ? ORDER BY v.last_viewed_at DESC LIMIT 15', (user_id,))
        user['orders'] = await self.all('SELECT * FROM orders WHERE user_id = ? ORDER BY id DESC LIMIT 15', (user_id,))
        user['purchases'] = await self.user_purchases(user_id)
        return user

    async def export_users(self) -> list[dict]:
        return await self.all(
            'SELECT u.id, u.username, u.first_name, u.language, u.created_at, u.last_active_at, u.is_blocked, '
            '(SELECT COUNT(*) FROM orders o WHERE o.user_id = u.id) AS orders, '
            "(SELECT COUNT(*) FROM orders o WHERE o.user_id = u.id AND o.status = 'paid') AS paid_orders "
            'FROM users u ORDER BY u.created_at')

    # -------------------------------------------------------------------- kurslar
    async def add_course(self, admin_id: int, data: dict) -> int:
        now = ts()
        values = {k: data.get(k, '') for k in COURSE_FIELDS}
        async with self.tx() as conn:
            cur = await conn.execute(
                'INSERT INTO courses(name_uz, name_ru, description_uz, description_ru, price_uzs, channel_chat_id, '
                'created_at, updated_at) VALUES (?,?,?,?,?,?,?,?)',
                (*[values[k] for k in COURSE_FIELDS], now, now))
            course_id = cur.lastrowid
            await self._log(conn, admin_id, 'course_add', {'course_id': course_id, **values})
        return course_id

    async def update_course(self, admin_id: int, course_id: int, field: str, value: Any) -> bool:
        if field not in COURSE_FIELDS:
            raise ValueError('Ruxsat etilmagan maydon')
        async with self.tx() as conn:
            cur = await conn.execute(f'UPDATE courses SET {field} = ?, updated_at = ? WHERE id = ? AND is_deleted = 0',
                                     (value, ts(), course_id))
            if cur.rowcount:
                await self._log(conn, admin_id, 'course_edit', {'course_id': course_id, 'field': field, 'value': value})
            return bool(cur.rowcount)

    async def toggle_course(self, admin_id: int, course_id: int) -> bool | None:
        async with self.tx() as conn:
            cur = await conn.execute('UPDATE courses SET is_active = 1 - is_active, updated_at = ? '
                                     'WHERE id = ? AND is_deleted = 0', (ts(), course_id))
            if not cur.rowcount:
                return None
            async with conn.execute('SELECT is_active FROM courses WHERE id = ?', (course_id,)) as c:
                active = bool((await c.fetchone())[0])
            await self._log(conn, admin_id, 'course_toggle', {'course_id': course_id, 'is_active': active})
            return active

    async def delete_course(self, admin_id: int, course_id: int) -> bool:
        """Kurs jismonan o‘chirilmaydi: buyurtma va xaridlar tarixi saqlanadi."""
        async with self.tx() as conn:
            cur = await conn.execute('UPDATE courses SET is_deleted = 1, is_active = 0, updated_at = ? '
                                     'WHERE id = ? AND is_deleted = 0', (ts(), course_id))
            if cur.rowcount:
                await self._log(conn, admin_id, 'course_delete', {'course_id': course_id})
            return bool(cur.rowcount)

    async def get_course(self, course_id: int, include_deleted: bool = False) -> dict | None:
        sql = 'SELECT * FROM courses WHERE id = ?' + ('' if include_deleted else ' AND is_deleted = 0')
        return await self.one(sql, (course_id,))

    async def list_courses(self, page: int, per_page: int = 8, active_only: bool = True) -> tuple[list[dict], int]:
        where = 'is_deleted = 0' + (' AND is_active = 1' if active_only else '')
        rows = await self.all(f'SELECT * FROM courses WHERE {where} ORDER BY id LIMIT ? OFFSET ?',
                              (per_page, page * per_page))
        return rows, await self.value(f'SELECT COUNT(*) FROM courses WHERE {where}')

    # ------------------------------------------------------------------ buyurtma
    async def get_or_create_open_order(self, user_id: int, course_id: int) -> tuple[str, dict | None]:
        """Natija: ('purchased'|'unavailable'|'existing'|'created', yozuv).

        Bir tranzaksiyada bajariladi: parallel bosishlar ikkinchi buyurtma yaratmaydi.
        """
        async with self.tx() as conn:
            async with conn.execute('SELECT * FROM purchases WHERE user_id = ? AND course_id = ?',
                                    (user_id, course_id)) as cur:
                purchase = await cur.fetchone()
            if purchase:
                return 'purchased', dict(purchase)
            async with conn.execute('SELECT * FROM courses WHERE id = ? AND is_active = 1 AND is_deleted = 0',
                                    (course_id,)) as cur:
                course = await cur.fetchone()
            if not course:
                return 'unavailable', None
            async with conn.execute("SELECT * FROM orders WHERE user_id = ? AND course_id = ? "
                                    "AND status IN ('new','pending')", (user_id, course_id)) as cur:
                order = await cur.fetchone()
            if order:
                if order['status'] == 'new' and order['amount_uzs'] != course['price_uzs']:
                    # Hisob hali yaratilmagan: joriy narxni qo‘llaymiz.
                    await conn.execute('UPDATE orders SET amount_uzs = ?, updated_at = ? WHERE id = ?',
                                       (course['price_uzs'], ts(), order['id']))
                    return 'existing', {**dict(order), 'amount_uzs': course['price_uzs']}
                return 'existing', dict(order)
            reference = 'ORD-' + uuid.uuid4().hex
            now = ts()
            cur = await conn.execute(
                'INSERT INTO orders(user_id, course_id, amount_uzs, course_title, external_reference, '
                "idempotency_key, status, created_at, updated_at) VALUES (?,?,?,?,?,?,'new',?,?)",
                (user_id, course_id, course['price_uzs'], course['name_uz'] or course['name_ru'],
                 reference, 'bill-' + reference, now, now))
            order_id = cur.lastrowid
        return 'created', await self.get_order(order_id)

    async def get_order(self, order_id: int) -> dict | None:
        return await self.one('SELECT * FROM orders WHERE id = ?', (order_id,))

    async def get_order_by_reference(self, reference: str) -> dict | None:
        return await self.one('SELECT * FROM orders WHERE external_reference = ?', (reference,))

    async def set_order_bill(self, order_id: int, bill_id: str, payment_url: str) -> bool:
        cur = await self.write(
            "UPDATE orders SET tezcheck_bill_id = ?, payment_url = ?, status = 'pending', last_error = NULL, "
            "updated_at = ? WHERE id = ? AND status = 'new'", (bill_id, payment_url, ts(), order_id))
        return bool(cur.rowcount)

    async def note_order_check(self, order_id: int, error: str | None = None) -> None:
        await self.write('UPDATE orders SET last_checked_at = ?, last_error = ? WHERE id = ?',
                         (ts(), error, order_id))

    async def set_order_status(self, order_id: int, status: str, from_statuses: Iterable[str],
                               error: str | None = None) -> bool:
        if status not in ORDER_STATUSES:
            raise ValueError(status)
        allowed = tuple(from_statuses)
        marks = ','.join('?' * len(allowed))
        cur = await self.write(
            f'UPDATE orders SET status = ?, last_error = COALESCE(?, last_error), updated_at = ? '
            f'WHERE id = ? AND status IN ({marks})', (status, error, ts(), order_id, *allowed))
        return bool(cur.rowcount)

    async def mark_paid(self, order_id: int, payment_id: str, environment: str, bill_id: str | None = None) -> PaidResult:
        """To‘lovni tasdiqlash va xarid yaratish — bitta atomar tranzaksiya. Takroriy chaqiruv xavfsiz."""
        async with self.tx() as conn:
            async with conn.execute('SELECT * FROM orders WHERE id = ?', (order_id,)) as cur:
                order = await cur.fetchone()
            if not order:
                raise LookupError(f'order {order_id} not found')
            if order['tezcheck_payment_id'] and order['tezcheck_payment_id'] != payment_id:
                raise ValueError('payment id mismatch')
            async with conn.execute('SELECT id, order_id FROM purchases WHERE user_id = ? AND course_id = ?',
                                    (order['user_id'], order['course_id'])) as cur:
                purchase = await cur.fetchone()
            if order['status'] == 'paid':
                if purchase and purchase['order_id'] == order_id:
                    return PaidResult(False, purchase['id'])
                return PaidResult(False, None, duplicate=True)
            now = ts()
            await conn.execute(
                "UPDATE orders SET status = 'paid', paid_at = ?, tezcheck_payment_id = ?, payment_environment = ?, "
                'tezcheck_bill_id = COALESCE(tezcheck_bill_id, ?), last_error = NULL, updated_at = ? WHERE id = ?',
                (now, payment_id, environment, bill_id, now, order_id))
            if purchase:
                await conn.execute('UPDATE orders SET last_error = ? WHERE id = ?',
                                   ('duplicate_payment', order_id))
                return PaidResult(True, None, duplicate=True)
            cur = await conn.execute(
                'INSERT INTO purchases(user_id, course_id, order_id, status, created_at, updated_at) '
                "VALUES (?,?,?,'pending_invite',?,?)", (order['user_id'], order['course_id'], order_id, now, now))
            return PaidResult(True, cur.lastrowid)

    async def orders_to_check(self, limit: int = 30, max_age_days: int = 35) -> list[dict]:
        return await self.all(
            "SELECT * FROM orders WHERE status = 'pending' AND tezcheck_bill_id IS NOT NULL AND created_at >= ? "
            'ORDER BY COALESCE(last_checked_at, \'\') LIMIT ?', (ts(utcnow() - timedelta(days=max_age_days)), limit))

    async def list_orders(self, status: str | None, page: int, per_page: int = 10, course_id: int | None = None,
                          user_id: int | None = None) -> tuple[list[dict], int]:
        where, params = ['1 = 1'], []
        if status:
            where.append('o.status = ?'), params.append(status)
        if course_id:
            where.append('o.course_id = ?'), params.append(course_id)
        if user_id:
            where.append('o.user_id = ?'), params.append(user_id)
        clause = ' AND '.join(where)
        rows = await self.all(
            f'SELECT o.*, p.status AS purchase_status FROM orders o LEFT JOIN purchases p ON p.order_id = o.id '
            f'WHERE {clause} ORDER BY o.id DESC LIMIT ? OFFSET ?', (*params, per_page, page * per_page))
        return rows, await self.value(f'SELECT COUNT(*) FROM orders o WHERE {clause}', params)

    async def stats(self) -> dict:
        return await self.one(
            'SELECT (SELECT COUNT(*) FROM courses WHERE is_deleted = 0) AS courses, '
            '(SELECT COUNT(*) FROM courses WHERE is_deleted = 0 AND is_active = 1) AS active_courses, '
            '(SELECT COUNT(*) FROM orders) AS orders, '
            "(SELECT COUNT(*) FROM orders WHERE status = 'paid') AS paid, "
            "(SELECT COUNT(*) FROM orders WHERE status = 'pending') AS pending, "
            "(SELECT COALESCE(SUM(amount_uzs), 0) FROM orders WHERE status = 'paid') AS revenue, "
            '(SELECT COUNT(*) FROM users) AS users, '
            "(SELECT COUNT(*) FROM purchases WHERE status <> 'sent') AS undelivered")

    # -------------------------------------------------------------------- xarid
    async def get_purchase(self, purchase_id: int) -> dict | None:
        return await self.one('SELECT * FROM purchases WHERE id = ?', (purchase_id,))

    async def purchase_for(self, user_id: int, course_id: int) -> dict | None:
        return await self.one('SELECT * FROM purchases WHERE user_id = ? AND course_id = ?', (user_id, course_id))

    async def purchase_by_order(self, order_id: int) -> dict | None:
        return await self.one('SELECT * FROM purchases WHERE order_id = ?', (order_id,))

    async def user_purchases(self, user_id: int) -> list[dict]:
        return await self.all(
            'SELECT p.*, c.name_uz, c.name_ru FROM purchases p JOIN courses c ON c.id = p.course_id '
            'WHERE p.user_id = ? ORDER BY p.id DESC', (user_id,))

    async def claim_purchase(self, purchase_id: int, statuses: Iterable[str]) -> dict | None:
        """Yetkazishni atomar “band qilish”: bir xaridni ikki vazifa bir vaqtda yubormaydi."""
        allowed = tuple(statuses)
        marks = ','.join('?' * len(allowed))
        async with self.tx() as conn:
            cur = await conn.execute(
                f"UPDATE purchases SET status = 'sending', attempts = attempts + 1, updated_at = ? "
                f'WHERE id = ? AND status IN ({marks})', (ts(), purchase_id, *allowed))
            if not cur.rowcount:
                return None
            async with conn.execute(
                    'SELECT p.*, o.status AS order_status, c.channel_chat_id, c.name_uz, c.name_ru '
                    'FROM purchases p JOIN orders o ON o.id = p.order_id JOIN courses c ON c.id = p.course_id '
                    'WHERE p.id = ?', (purchase_id,)) as c:
                return dict(await c.fetchone())

    async def save_invite(self, purchase_id: int, link: str, expires_at: datetime) -> None:
        await self.write("UPDATE purchases SET invite_link = ?, invite_expires_at = ?, status = 'link_created', "
                         'invite_count = invite_count + 1, last_error = NULL, updated_at = ? WHERE id = ?',
                         (link, ts(expires_at), ts(), purchase_id))

    async def mark_purchase_sent(self, purchase_id: int) -> None:
        await self.write("UPDATE purchases SET status = 'sent', invite_sent_at = ?, last_error = NULL, "
                         'next_retry_at = NULL, updated_at = ? WHERE id = ?', (ts(), ts(), purchase_id))

    async def mark_purchase_failed(self, purchase_id: int, status: str, error: str,
                                   retry_at: datetime | None) -> None:
        await self.write('UPDATE purchases SET status = ?, last_error = ?, next_retry_at = ?, updated_at = ? '
                         'WHERE id = ?', (status, error[:300], ts(retry_at) if retry_at else None, ts(), purchase_id))

    async def release_purchase(self, purchase_id: int, status: str) -> None:
        await self.write("UPDATE purchases SET status = ?, updated_at = ? WHERE id = ? AND status = 'sending'",
                         (status, ts(), purchase_id))

    async def due_deliveries(self, limit: int = 20) -> list[dict]:
        """Avtomatik qayta urinish kerak bo‘lgan xaridlar (kanal xatosi admin tuzatishini kutadi)."""
        return await self.all(
            "SELECT * FROM purchases WHERE status IN ('pending_invite','link_created','send_failed') "
            'AND (next_retry_at IS NULL OR next_retry_at <= ?) ORDER BY id LIMIT ?', (ts(), limit))

    async def undelivered(self, page: int, per_page: int = 10) -> tuple[list[dict], int]:
        rows = await self.all(
            "SELECT p.*, c.name_uz, c.name_ru FROM purchases p JOIN courses c ON c.id = p.course_id "
            "WHERE p.status <> 'sent' ORDER BY p.id DESC LIMIT ? OFFSET ?", (per_page, page * per_page))
        return rows, await self.value("SELECT COUNT(*) FROM purchases WHERE status <> 'sent'")

    async def reset_stuck_deliveries(self) -> int:
        """Jarayon to‘satdan to‘xtagan bo‘lsa, “sending” holatini qayta urinishga qaytaramiz."""
        cur = await self.write(
            "UPDATE purchases SET status = CASE WHEN invite_link IS NULL THEN 'pending_invite' ELSE 'link_created' END, "
            "updated_at = ? WHERE status = 'sending'", (ts(),))
        return cur.rowcount

    # ------------------------------------------------------------ webhook hodisa
    async def register_webhook_event(self, event_id: str, event_type: str, delivery_id: str) -> str | None:
        """Yangi hodisa bo‘lsa None, aks holda mavjud holatni qaytaradi."""
        async with self.tx() as conn:
            async with conn.execute('SELECT status FROM webhook_events WHERE event_id = ?', (event_id,)) as cur:
                row = await cur.fetchone()
            if row:
                return row['status']
            await conn.execute("INSERT INTO webhook_events(event_id, event_type, delivery_id, received_at, status) "
                               "VALUES (?,?,?,?,'received')", (event_id, event_type[:64], delivery_id[:128], ts()))
            return None

    async def finish_webhook_event(self, event_id: str, status: str, error: str | None = None) -> None:
        await self.write('UPDATE webhook_events SET status = ?, processed_at = ?, error = ? WHERE event_id = ?',
                         (status, ts(), (error or None) and error[:300], event_id))

    async def get_webhook_event(self, event_id: str) -> dict | None:
        return await self.one('SELECT * FROM webhook_events WHERE event_id = ?', (event_id,))
