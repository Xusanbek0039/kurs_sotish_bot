# Barcha fayllarning kodi

## main.py
````text
import asyncio
import contextlib
import logging
import time
from datetime import datetime, timedelta, timezone
from html import escape

from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.exceptions import TelegramAPIError
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import BotCommand, BotCommandScopeChat, InlineKeyboardButton, InlineKeyboardMarkup, Message

from config import Config
from database import Database
from tezcheck import PaymentError, Tezcheck

log = logging.getLogger('courses')


def keyboard(items):
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text=text, **({'url': value} if value.startswith('https://') else {'callback_data': value}))
    ] for text, value in items])


def money(value):
    return f'{value:,}'.replace(',', ' ') + ' so‘m'


class CourseForm(StatesGroup):
    name = State()
    description = State()
    price = State()
    channel = State()
    confirm = State()


class Shop:
    def __init__(self, config, db, api, bot):
        self.cfg, self.db, self.api, self.bot = config, db, api, bot
        self.router = Router()
        self.payment_lock = asyncio.Lock()
        self.username = ''
        r = self.router
        r.message.register(self.start, CommandStart())
        r.message.register(self.cancel, Command('cancel'))
        r.message.register(self.admin_command, Command('admin'))
        r.message.register(self.health, Command('health'))
        r.message.register(self.whoami, Command('id'))
        r.callback_query.register(self.admin_callback, F.data.startswith('a:'))
        r.callback_query.register(self.customer_callback, F.data.startswith('u:'))
        r.message.register(self.form, CourseForm.name)
        r.message.register(self.form, CourseForm.description)
        r.message.register(self.form, CourseForm.price)
        r.message.register(self.form, CourseForm.channel)
        r.message.register(self.form, CourseForm.confirm)
        r.chat_join_request.register(self.join)
        r.errors.register(self.error)

    async def error(self, event):
        log.error('Handler xatosi: %s', type(event.exception).__name__)
        return True

    async def whoami(self, message):
        await message.answer(f'Telegram ID: <code>{message.from_user.id}</code>')

    async def start(self, message: Message):
        if message.chat.type != 'private':
            return
        parts = (message.text or '').split(maxsplit=1)
        if len(parts) == 2 and parts[1].startswith('course_'):
            try:
                await self.show_course(message, int(parts[1][7:]))
            except ValueError:
                await message.answer('Kurs havolasi noto‘g‘ri.')
            return
        rows = await self.db.rows('SELECT * FROM courses WHERE active=1 AND deleted=0 ORDER BY id')
        items = [(f'📚 {c["name"]} — {money(c["price"])}', f'u:course:{c["id"]}') for c in rows]
        if message.from_user.id in self.cfg.admins:
            items.append(('⚙️ Admin panel', 'a:menu'))
        emoji = f'<tg-emoji emoji-id="{self.cfg.emoji}">📚</tg-emoji>' if self.cfg.emoji else '📚'
        await message.answer(f'{emoji} Assalomu alaykum! Kursni tanlang.' if rows else 'Hozircha faol kurslar yo‘q.',
                             reply_markup=keyboard(items) if items else None)

    async def show_course(self, message, cid):
        c = await self.db.one('SELECT * FROM courses WHERE id=? AND active=1 AND deleted=0', (cid,))
        if not c:
            await message.answer('Kurs topilmadi yoki sotuvda emas.')
            return
        await message.answer(f'<b>{escape(c["name"])}</b>\n\n{escape(c["description"])}\n\nNarxi: {money(c["price"])}',
                             reply_markup=keyboard([('💳 To‘lov qilish', f'u:buy:{cid}')]))

    async def customer_callback(self, call):
        await call.answer()
        if call.message.chat.type != 'private':
            return
        try:
            _, action, raw = call.data.split(':')
            ident = int(raw)
            if action == 'course':
                await self.show_course(call.message, ident)
                return
            async with self.payment_lock:
                if action == 'buy':
                    o = await self.db.order(call.from_user.id, ident)
                    if o['status'] == 'paid':
                        await self.deliver(o, repeat=True)
                        return
                    if o['status'] == 'review':
                        raise PaymentError('Buyurtma qo‘lda solishtirilishi kerak. Adminga buyurtma raqamini yuboring: ' + str(o['id']))
                    if not o['bill_id']:
                        age = datetime.now(timezone.utc) - datetime.fromisoformat(o['created_at']).replace(tzinfo=timezone.utc)
                        if age > timedelta(hours=23):
                            await self.db.execute("UPDATE orders SET status='review' WHERE id=?", (o['id'],))
                            await self.alert(o, 'Eski noaniq buyurtma: Tezcheck kabinetida external_reference bo‘yicha solishtiring.')
                            raise PaymentError('Eski hisob holati noaniq. Adminga murojaat qiling.')
                        data = await self.api.create(o)
                        await self.db.execute("UPDATE orders SET bill_id=?,payment_url=?,status='pending' WHERE id=?",
                                              (data['bill']['id'], data['payment_url'], o['id']))
                        o = await self.db.one('SELECT * FROM orders WHERE id=?', (o['id'],))
                    await call.message.answer(f'Buyurtma #{o["id"]}. Summa: {money(o["amount"])}\nTo‘lovdan so‘ng tekshirish tugmasini bosing.',
                        reply_markup=keyboard([('💳 To‘lov sahifasi', o['payment_url']), ('🔄 To‘lovni tekshirish', f'u:check:{o["id"]}')]))
                elif action == 'check':
                    o = await self.db.one('SELECT * FROM orders WHERE id=? AND user_id=?', (ident, call.from_user.id))
                    if not o or not o['bill_id']:
                        raise PaymentError('Buyurtma topilmadi.')
                    if o['status'] == 'paid':
                        await self.deliver(o, repeat=True)
                    elif await self.verify(o):
                        await self.deliver(o)
                    else:
                        await call.message.answer('To‘lov hali tasdiqlanmadi. Birozdan keyin qayta tekshiring.')
        except (PaymentError, ValueError) as exc:
            await call.message.answer(escape(str(exc)))

    async def verify(self, o):
        await self.db.execute('UPDATE orders SET last_checked=CURRENT_TIMESTAMP WHERE id=?', (o['id'],))
        paid, pid = await self.api.check(o)
        if paid:
            await self.db.mark_paid(o, pid)
        return paid

    async def alert(self, order, reason):
        log.warning('Buyurtma %s: %s', order['id'], reason)
        for admin in self.cfg.admins:
            with contextlib.suppress(TelegramAPIError):
                await self.bot.send_message(admin, f'⚠️ Buyurtma #{order["id"]}, foydalanuvchi {order["user_id"]}.\n{escape(reason)}')

    async def deliver(self, o, repeat=False):
        p = await self.db.one('SELECT * FROM purchases WHERE order_id=?', (o['id'],))
        if not p:
            return
        if p['status'] == 'joined':
            if repeat:
                await self.bot.send_message(o['user_id'], 'Siz ushbu kursni xarid qilgansiz va kanalga kirgansiz.')
            return
        if p['status'] in ('creating', 'error'):
            if repeat:
                await self.bot.send_message(o['user_id'], 'To‘lov tasdiqlangan. Kanalga kirish uchun /id orqali IDingizni olib, adminga murojaat qiling.')
            return
        if p['invite_link'] and p['expires_at'] <= int(time.time()):
            if repeat:
                await self.bot.send_message(o['user_id'], 'Xaridingiz saqlangan. Havola muddati tugagan; adminga murojaat qiling.')
            return
        if not p['invite_link']:
            # Persist before Telegram call: a crash must never trigger a second invite automatically.
            await self.db.execute("UPDATE purchases SET status='creating' WHERE id=?", (p['id'],))
            expiry = datetime.now(timezone.utc) + timedelta(hours=self.cfg.invite_hours)
            try:
                invite = await self.bot.create_chat_invite_link(o['channel_chat_id'],
                    name=f'order-{o["id"]}', expire_date=expiry, creates_join_request=True)
                await self.db.execute("UPDATE purchases SET invite_link=?,expires_at=?,status='ready' WHERE id=?",
                                      (invite.invite_link, int(expiry.timestamp()), p['id']))
                p = await self.db.one('SELECT * FROM purchases WHERE id=?', (p['id'],))
            except TelegramAPIError as exc:
                await self.db.execute("UPDATE purchases SET status='error' WHERE id=?", (p['id'],))
                await self.alert(o, 'Kanal taklifini yaratib bo‘lmadi: ' + type(exc).__name__)
                with contextlib.suppress(TelegramAPIError):
                    await self.bot.send_message(o['user_id'], 'To‘lov tasdiqlandi. Kanalga kirish uchun adminga murojaat qiling. Buyurtma #' + str(o['id']))
                return
        if repeat or not p['notified']:
            await self.bot.send_message(o['user_id'], '✅ Kurs xarid qilingan. Havolani ochib kirish so‘rovini yuboring. Faqat sizning IDingiz tasdiqlanadi.',
                                        reply_markup=keyboard([('🔐 Kurs kanaliga kirish', p['invite_link'])]))
            await self.db.execute('UPDATE purchases SET notified=1 WHERE id=?', (p['id'],))

    async def join(self, request):
        link = request.invite_link.invite_link if request.invite_link else None
        if not link:
            return
        p = await self.db.one("SELECT p.*,o.channel_chat_id FROM purchases p JOIN orders o ON o.id=p.order_id WHERE p.invite_link=? AND o.status='paid'", (link,))
        if not p:
            return
        if (p['user_id'] != request.from_user.id or p['channel_chat_id'] != request.chat.id or
                p['status'] != 'ready' or p['expires_at'] <= int(time.time())):
            await request.decline()
            return
        await request.approve()
        await self.db.execute("UPDATE purchases SET status='joined' WHERE id=?", (p['id'],))
        with contextlib.suppress(TelegramAPIError):
            await self.bot.revoke_chat_invite_link(request.chat.id, link)

    async def cancel(self, message, state: FSMContext):
        await state.clear()
        await message.answer('Kiritish bekor qilindi.')

    async def admin_command(self, message, state: FSMContext):
        if message.from_user.id not in self.cfg.admins or message.chat.type != 'private':
            return
        await state.clear()
        await self.admin_menu(message, message.from_user.id)

    async def admin_menu(self, message, uid):
        await self.db.audit(uid, 'menu', {})
        await message.answer('⚙️ Admin panel', reply_markup=keyboard([
            ('➕ Kurs qo‘shish', 'a:add'), ('📚 Kurslar', 'a:list:0'),
            ('🧾 Buyurtmalar va to‘lovlar', 'a:orders:0'), ('📊 Statistika', 'a:stats')]))

    async def admin_callback(self, call, state: FSMContext):
        if call.from_user.id not in self.cfg.admins or call.message.chat.type != 'private':
            await call.answer('Ruxsat yo‘q.', show_alert=True)
            return
        await call.answer()
        bits = call.data.split(':')
        action = bits[1]
        uid = call.from_user.id
        await self.db.audit(uid, 'button:' + action, {'data': call.data})
        if action == 'menu':
            await state.clear()
            await self.admin_menu(call.message, uid)
        elif action == 'add':
            await state.clear()
            await state.set_state(CourseForm.name)
            await call.message.answer('Kurs nomini kiriting (2–120 belgi). Bekor qilish: /cancel')
        elif action == 'list':
            page = max(0, int(bits[2]))
            rows = await self.db.rows('SELECT * FROM courses WHERE deleted=0 ORDER BY id LIMIT 10 OFFSET ?', (page * 10,))
            items = [(f'#{c["id"]} {c["name"]} ({"faol" if c["active"] else "nofaol"})', f'a:course:{c["id"]}') for c in rows]
            if page:
                items.append(('⬅️ Oldingi', f'a:list:{page-1}'))
            if len(rows) == 10:
                items.append(('➡️ Keyingi', f'a:list:{page+1}'))
            await call.message.answer('Kurslar:', reply_markup=keyboard(items or [('Admin panel', 'a:menu')]))
        elif action == 'orders':
            page = max(0, int(bits[2]))
            rows = await self.db.rows('SELECT o.*,p.status AS access FROM orders o LEFT JOIN purchases p ON p.order_id=o.id ORDER BY o.id DESC LIMIT 10 OFFSET ?', (page * 10,))
            text = '\n'.join(f'#{o["id"]} | user {o["user_id"]} | kurs {o["course_id"]}\n{money(o["amount"])} | {o["status"]} | kirish: {o["access"] or "yo‘q"}\nref: {o["external_reference"]}' for o in rows)
            items = [('Admin panel', 'a:menu')]
            if page:
                items.append(('⬅️ Oldingi', f'a:orders:{page-1}'))
            if len(rows) == 10:
                items.append(('➡️ Keyingi', f'a:orders:{page+1}'))
            await call.message.answer(escape(text or 'Buyurtmalar yo‘q.'), reply_markup=keyboard(items))
        elif action == 'stats':
            c = await self.db.one('SELECT COUNT(*) AS n FROM courses WHERE deleted=0')
            o = await self.db.one("SELECT COUNT(*) AS n,COALESCE(SUM(status='paid'),0) AS paid,COALESCE(SUM(CASE WHEN status='paid' THEN amount ELSE 0 END),0) AS revenue FROM orders")
            await call.message.answer(f'Kurslar: {c["n"]}\nBuyurtmalar: {o["n"]}\nMuvaffaqiyatli to‘lovlar: {o["paid"]}\nTushum (yalpi): {money(o["revenue"])}')
        elif action == 'save':
            if await state.get_state() != CourseForm.confirm.state:
                await call.message.answer('Kiritish muddati tugagan. /admin orqali qayta boshlang.')
                return
            d = await state.get_data()
            if d.get('edit_id'):
                await self.db.course_change(uid, 'edit', 'UPDATE courses SET name=?,description=?,price=?,channel_chat_id=? WHERE id=? AND deleted=0',
                    (d['name'], d['description'], d['price'], d['channel'], d['edit_id']))
            else:
                await self.db.course_change(uid, 'add', 'INSERT INTO courses(name,description,price,channel_chat_id) VALUES(?,?,?,?)',
                    (d['name'], d['description'], d['price'], d['channel']))
            await state.clear()
            await call.message.answer('✅ Kurs saqlandi.')
        elif len(bits) >= 3:
            cid = int(bits[2])
            c = await self.db.one('SELECT * FROM courses WHERE id=? AND deleted=0', (cid,))
            if not c:
                await call.message.answer('Kurs topilmadi.')
                return
            if action == 'course':
                await call.message.answer(f'#{cid} <b>{escape(c["name"])}</b>\n{escape(c["description"])}\n{money(c["price"])}\nKanal: <code>{c["channel_chat_id"]}</code>\nHolat: {"faol" if c["active"] else "nofaol"}\nhttps://t.me/{self.username}?start=course_{cid}',
                    reply_markup=keyboard([('✏️ Tahrirlash', f'a:edit:{cid}'), ('🔄 Faol/nofaol', f'a:toggle:{cid}'), ('🗑 O‘chirish', f'a:delete:{cid}')]))
            elif action == 'edit':
                await state.clear()
                await state.update_data(edit_id=cid)
                await state.set_state(CourseForm.name)
                await call.message.answer('Yangi kurs nomini kiriting. Keyin tavsif, narx va kanal qayta kiritiladi. /cancel')
            elif action == 'toggle':
                await self.db.course_change(uid, 'toggle', 'UPDATE courses SET active=1-active WHERE id=?', (cid,))
                await call.message.answer('Kurs holati o‘zgartirildi.')
            elif action == 'delete':
                await call.message.answer('Kursni o‘chirishni tasdiqlaysizmi? Xaridlar tarixi saqlanadi.',
                                          reply_markup=keyboard([('Ha, o‘chirish', f'a:remove:{cid}'), ('Bekor qilish', f'a:course:{cid}')]))
            elif action == 'remove':
                await self.db.course_change(uid, 'delete', 'UPDATE courses SET deleted=1,active=0 WHERE id=?', (cid,))
                await call.message.answer('Kurs o‘chirildi. Eski to‘lovlar uchun kirish huquqi saqlanadi.')

    async def form(self, message, state: FSMContext):
        if message.from_user.id not in self.cfg.admins or message.chat.type != 'private':
            return
        current = await state.get_state()
        text = (message.text or '').strip()
        if current == CourseForm.name.state:
            if not 2 <= len(text) <= 120:
                await message.answer('Nom 2–120 belgidan iborat bo‘lsin.')
                return
            await state.update_data(name=text)
            await state.set_state(CourseForm.description)
            await message.answer('Tavsifni kiriting (1–2000 belgi).')
        elif current == CourseForm.description.state:
            if not 1 <= len(text) <= 2000:
                await message.answer('Tavsif 1–2000 belgi bo‘lsin.')
                return
            await state.update_data(description=text)
            await state.set_state(CourseForm.price)
            await message.answer('Narxni butun so‘mda kiriting (masalan, 150000).')
        elif current == CourseForm.price.state:
            if not text.isascii() or not text.isdigit() or not 1000 <= int(text) <= 1_000_000_000:
                await message.answer('1000 dan 1000000000 gacha butun son kiriting. Kassa chegarasi Tezcheck tomonidan tekshiriladi.')
                return
            await state.update_data(price=int(text))
            await state.set_state(CourseForm.channel)
            await message.answer('Yopiq kanal chat_id qiymatini kiriting (odatda -100 bilan boshlanadi). Botni kanalga taklif huquqi bilan admin qiling.')
        elif current == CourseForm.channel.state:
            try:
                cid = int(text)
                chat = await self.bot.get_chat(cid)
                member = await self.bot.get_chat_member(cid, self.bot.id)
                if chat.type != 'channel' or chat.username or member.status != 'administrator' or not member.can_invite_users:
                    raise ValueError()
            except (ValueError, TelegramAPIError):
                await message.answer('Yopiq kanal topilmadi yoki bot admin emas/taklif huquqi yo‘q. Sozlab, chat_id ni qayta yuboring.')
                return
            await state.update_data(channel=cid)
            await state.set_state(CourseForm.confirm)
            d = await state.get_data()
            await message.answer(f'<b>{escape(d["name"])}</b>\n{escape(d["description"])}\n{money(d["price"])}\nKanal: {cid}\nSaqlansinmi?',
                                  reply_markup=keyboard([('✅ Tasdiqlash', 'a:save'), ('Bekor qilish', 'a:menu')]))
        else:
            await message.answer('Tasdiqlash tugmasini bosing yoki /cancel yuboring.')
        await self.db.audit(message.from_user.id, 'form', {'state': current})

    async def health(self, message):
        if message.from_user.id not in self.cfg.admins or message.chat.type != 'private':
            return
        await self.db.audit(message.from_user.id, 'health', {})
        await self.db.one('SELECT 1')
        try:
            data = await self.api.request('GET', '/me')
            desk = data.get('cash_desk')
            ok = isinstance(desk, dict) and str(desk.get('code')) == self.cfg.desk and desk.get('currency') == 'UZS'
            await message.answer('SQLite: ✅\nTelegram: ✅\nTezcheck: ' + ('✅ kalit va kassa mos.' if ok else '⚠️ kassa sozlamalarini tekshiring.'))
        except PaymentError as exc:
            await message.answer('SQLite: ✅\nTelegram: ✅\n' + escape(str(exc)))

    async def poll(self):
        while True:
            try:
                rows = await self.db.rows("SELECT * FROM orders WHERE status IN ('pending','paid') ORDER BY COALESCE(last_checked,'') ASC LIMIT 30")
                for o in rows:
                    try:
                        async with self.payment_lock:
                            if o['status'] == 'pending':
                                if await self.verify(o):
                                    await self.deliver(o)
                            else:
                                await self.db.execute('UPDATE orders SET last_checked=CURRENT_TIMESTAMP WHERE id=?', (o['id'],))
                                await self.deliver(o)
                    except (PaymentError, TelegramAPIError) as exc:
                        log.warning('Tekshirish xatosi: buyurtma=%s tur=%s', o['id'], type(exc).__name__)
                    await asyncio.sleep(1)
            except Exception as exc:
                log.error('Fon vazifasi: %s', type(exc).__name__)
            await asyncio.sleep(self.cfg.interval)


async def main():
    cfg = Config.load()
    db = Database(cfg.database)
    await db.open()
    bot = Bot(cfg.token, default=DefaultBotProperties(parse_mode='HTML'))
    api = Tezcheck(cfg)
    task = None
    try:
        # Interrupted invite creation is intentionally handled by an admin, never recreated blindly.
        await db.execute("UPDATE purchases SET status='error' WHERE status='creating'")
        shop = Shop(cfg, db, api, bot)
        shop.username = (await bot.get_me()).username
        await bot.set_my_commands([BotCommand(command='start', description='Kurslar'), BotCommand(command='id', description='Telegram ID')])
        for admin in cfg.admins:
            with contextlib.suppress(TelegramAPIError):
                await bot.set_my_commands([BotCommand(command='start', description='Kurslar'), BotCommand(command='admin', description='Admin panel'),
                    BotCommand(command='health', description='Ulanishni tekshirish'), BotCommand(command='id', description='Telegram ID')], scope=BotCommandScopeChat(chat_id=admin))
        dp = Dispatcher()
        dp.include_router(shop.router)
        task = asyncio.create_task(shop.poll())
        log.info('Bot ishga tushdi.')
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types(), close_bot_session=False,
                               handle_as_tasks=False)
    finally:
        if task:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        await api.close()
        await bot.session.close()
        await db.close()


if __name__ == '__main__':
    logging.basicConfig(level=logging.WARNING, format='%(asctime)s %(levelname)s %(name)s: %(message)s')
    logging.getLogger('courses').setLevel(logging.INFO)
    try:
        asyncio.run(main())
    except (ValueError, KeyboardInterrupt) as exc:
        if isinstance(exc, ValueError):
            print(str(exc))

````

## config.py
````text
import os
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent


@dataclass(frozen=True)
class Config:
    token: str
    admins: frozenset[int]
    api_key: str
    desk: str
    base_url: str
    database: str
    interval: int
    invite_hours: int
    emoji: str

    @classmethod
    def load(cls):
        load_dotenv(ROOT / '.env')
        required = ['BOT_TOKEN', 'ADMIN_IDS', 'TEZCHECK_API_KEY', 'TEZCHECK_CASH_DESK_CODE']
        if any(not os.getenv(k, '').strip() for k in required):
            raise ValueError('.env ichida BOT_TOKEN, ADMIN_IDS va Tezcheck sozlamalarini to‘ldiring.')
        admins = frozenset(int(x.strip()) for x in os.environ['ADMIN_IDS'].split(',') if x.strip())
        if not admins or any(x <= 0 for x in admins):
            raise ValueError('ADMIN_IDS musbat Telegram IDlardan iborat bo‘lsin.')
        base = os.getenv('TEZCHECK_BASE_URL', 'https://api.tezcheck.uz/api/merchant/v1').rstrip('/')
        if urlparse(base).scheme != 'https' or not urlparse(base).hostname:
            raise ValueError('TEZCHECK_BASE_URL HTTPS manzil bo‘lishi kerak.')
        emoji = os.getenv('CUSTOM_EMOJI_ID', '').strip()
        if emoji and not emoji.isdigit():
            raise ValueError('CUSTOM_EMOJI_ID raqamlardan iborat yoki bo‘sh bo‘lsin.')
        interval = int(os.getenv('POLL_INTERVAL', '60'))
        hours = int(os.getenv('INVITE_TTL_HOURS', '24'))
        if interval < 30 or not 1 <= hours <= 168:
            raise ValueError('POLL_INTERVAL >= 30, INVITE_TTL_HOURS 1–168 bo‘lsin.')
        return cls(os.environ['BOT_TOKEN'].strip(), admins, os.environ['TEZCHECK_API_KEY'].strip(),
                   os.environ['TEZCHECK_CASH_DESK_CODE'].strip(), base,
                   str(ROOT / os.getenv('DATABASE_PATH', 'data/bot.db')), interval, hours, emoji)

````

## database.py
````text
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

````

## tezcheck.py
````text
import asyncio
import json
from urllib.parse import quote, urlparse

import aiohttp


class PaymentError(Exception):
    pass


class Tezcheck:
    def __init__(self, config):
        self.config = config
        self.session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=20), headers={
            'Authorization': 'Bearer ' + config.api_key,
            'X-Cash-Desk-Code': config.desk, 'Accept': 'application/json'})

    async def close(self):
        await self.session.close()

    async def request(self, method, path, body=None, key=None):
        headers = {'Content-Type': 'application/json'}
        if key:
            headers['Idempotency-Key'] = key
        try:
            async with self.session.request(method, self.config.base_url + path,
                    data=json.dumps(body).encode() if body is not None else None,
                    headers=headers, allow_redirects=False) as response:
                if not 200 <= response.status < 300:
                    raise PaymentError(f'Tezcheck HTTP {response.status}. Keyinroq qayta tekshiring yoki adminga murojaat qiling.')
                payload = await response.json()
                if not isinstance(payload, dict) or not isinstance(payload.get('data'), dict):
                    raise PaymentError('Tezcheck javobi kutilgan formatda emas.')
                return payload['data']
        except (aiohttp.ClientError, asyncio.TimeoutError, ValueError):
            raise PaymentError('Tezcheck bilan aloqa vaqtincha uzildi. Keyinroq qayta urinib ko‘ring.') from None

    @staticmethod
    def validate_bill(bill, order, existing=False):
        if not isinstance(bill, dict) or not isinstance(bill.get('id'), str) or not bill['id']:
            raise PaymentError('Hisob identifikatori noto‘g‘ri.')
        if (bill.get('external_reference') != order['external_reference'] or
                type(bill.get('amount_minor')) is not int or bill['amount_minor'] != order['amount'] * 100 or
                bill.get('currency') != 'UZS' or (existing and bill['id'] != order['bill_id'])):
            raise PaymentError('Hisob ma’lumotlari buyurtmaga mos kelmadi. Adminga murojaat qiling.')

    async def create(self, order):
        data = await self.request('POST', '/bills', {
            'amount_minor': order['amount'] * 100, 'title': order['title'],
            'external_reference': order['external_reference']}, order['external_reference'])
        self.validate_bill(data.get('bill'), order)
        url = data.get('payment_url')
        if not isinstance(url, str) or urlparse(url).scheme != 'https' or not urlparse(url).hostname:
            raise PaymentError('Xavfsiz payment_url qaytmadi. Adminga murojaat qiling.')
        return data

    async def check(self, order):
        data = await self.request('POST', '/bills/' + quote(order['bill_id'], safe=''))
        bill = data.get('bill')
        self.validate_bill(bill, order, True)
        if type(bill.get('paid')) is not bool or type(bill.get('livemode')) is not bool:
            raise PaymentError('To‘lov holati javobda yetishmayapti.')
        if not bill['paid']:
            return False, None
        if not bill['livemode'] or bill.get('environment') != 'live':
            raise PaymentError('Bu test to‘lovi. Haqiqiy kursga kirish berilmadi; adminga murojaat qiling.')
        payment = data.get('payment')
        if (not isinstance(payment, dict) or payment.get('state') != 'succeeded' or
            payment.get('livemode') is not True or payment.get('environment') != 'live' or
            type(payment.get('amount_minor')) is not int or payment['amount_minor'] != order['amount'] * 100 or
            payment.get('currency') != 'UZS' or payment.get('refunded_amount_minor') != 0 or
            not isinstance(payment.get('id'), str) or not payment['id']):
            raise PaymentError('To‘lovni xavfsiz tasdiqlash imkoni bo‘lmadi. Adminga murojaat qiling.')
        return True, payment['id']

````

## requirements.txt
````text
aiogram==3.29.0
aiosqlite==0.21.0
python-dotenv==1.1.1

````

## .env.example
````text
BOT_TOKEN=
ADMIN_IDS=123456789
TEZCHECK_API_KEY=
TEZCHECK_CASH_DESK_CODE=
TEZCHECK_BASE_URL=https://api.tezcheck.uz/api/merchant/v1
DATABASE_PATH=data/bot.db
POLL_INTERVAL=60
INVITE_TTL_HOURS=24
CUSTOM_EMOJI_ID=

````

## .gitignore
````text
.env
.venv/
__pycache__/
*.db
*.db-shm
*.db-wal

````

## tests/test_payments.py
````text
import asyncio
import tempfile
import unittest
from pathlib import Path

from database import Database
from tezcheck import PaymentError, Tezcheck


class PaymentTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Database(str(Path(self.tmp.name) / 'test.db'))
        await self.db.open()
        self.cid = await self.db.course_change(1, 'add',
            'INSERT INTO courses(name,description,price,channel_chat_id) VALUES(?,?,?,?)',
            ('Python', 'Darslar', 150000, -1001234567890))

    async def asyncTearDown(self):
        await self.db.close()
        self.tmp.cleanup()

    async def test_concurrent_checkout_and_restart(self):
        orders = await asyncio.gather(*(self.db.order(42, self.cid) for _ in range(10)))
        self.assertEqual(len({o['id'] for o in orders}), 1)
        await self.db.close()
        await self.db.open()
        self.assertEqual((await self.db.order(42, self.cid))['external_reference'], orders[0]['external_reference'])

    async def test_paid_is_atomic_and_idempotent(self):
        o = await self.db.order(42, self.cid)
        await self.db.mark_paid(o, 'pay_unique')
        await self.db.mark_paid(o, 'pay_unique')
        self.assertEqual(len(await self.db.rows('SELECT * FROM purchases')), 1)
        self.assertEqual((await self.db.order(42, self.cid))['status'], 'paid')

    async def test_order_snapshots_survive_course_edit_and_delete(self):
        o = await self.db.order(42, self.cid)
        await self.db.course_change(1, 'edit', 'UPDATE courses SET price=200000,channel_chat_id=-100999,deleted=1 WHERE id=?', (self.cid,))
        same = await self.db.order(42, self.cid)
        self.assertEqual(same['amount'], 150000)
        self.assertEqual(same['channel_chat_id'], o['channel_chat_id'])

    async def test_payment_validation(self):
        o = await self.db.order(42, self.cid)
        o['bill_id'] = 'link_1'
        client = object.__new__(Tezcheck)
        bill = {'id': 'link_1', 'external_reference': o['external_reference'], 'amount_minor': 15000000,
                'currency': 'UZS', 'paid': True, 'livemode': True, 'environment': 'live', 'state': 'expired'}
        payment = {'id': 'pay_1', 'state': 'succeeded', 'amount_minor': 15000000, 'currency': 'UZS',
                   'livemode': True, 'environment': 'live', 'refunded_amount_minor': 0}

        async def response(*args, **kwargs):
            return {'bill': bill, 'payment': payment}

        client.request = response
        self.assertEqual(await client.check(o), (True, 'pay_1'))
        bill['livemode'] = False
        with self.assertRaises(PaymentError):
            await client.check(o)
        bill['livemode'] = True
        payment['amount_minor'] = 1
        with self.assertRaises(PaymentError):
            await client.check(o)
        payment['amount_minor'] = 15000000
        bill['external_reference'] = 'OTHER'
        with self.assertRaises(PaymentError):
            await client.check(o)

    async def test_unpaid_never_passes(self):
        o = await self.db.order(42, self.cid)
        o['bill_id'] = 'link_1'
        client = object.__new__(Tezcheck)

        async def response(*args, **kwargs):
            return {'bill': {'id': 'link_1', 'external_reference': o['external_reference'], 'amount_minor': 15000000,
                             'currency': 'UZS', 'paid': False, 'livemode': True}}

        client.request = response
        self.assertEqual(await client.check(o), (False, None))
        self.assertFalse(await self.db.rows('SELECT * FROM purchases'))


if __name__ == '__main__':
    unittest.main()

````

## README.md
````text
# Telegram orqali kurs sotish

Python 3.11+ va SQLite asosidagi aiogram 3 bot. Boshlash buyrug‘i: `python main.py`.

## Tuzilishi

```text
kurs_sotish_bot/
  main.py                  Telegram menyulari, admin suhbatlari va fon vazifasi
  config.py                .env konfiguratsiyasi
  database.py              SQLite sxemasi va tranzaksiyalar
  tezcheck.py               Merchant API mijozi
  requirements.txt
  .env.example
  .gitignore
  README.md
  tests/test_payments.py
  data/bot.db               birinchi ishga tushirishda yaratiladi
```

## Windows (PowerShell)

Python 3.11 yoki yangiroq versiyani python.org saytidan o‘rnating va PATH belgisini yoqing.

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
Copy-Item .env.example .env
notepad .env
python main.py
```

Agar PowerShell aktivlashtirishni cheklasa, virtual muhitdagi Pythonni to‘g‘ridan-to‘g‘ri chaqiring:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe main.py
```

## Linux

Python 3.11+ va distributivingizdagi Python venv paketini o‘rnating.

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
cp .env.example .env
nano .env
python main.py
```

## Sozlash

- `BOT_TOKEN`: Telegramdagi rasmiy @BotFather orqali `/newbot` bilan yarating.
- `ADMIN_IDS`: adminlarning raqamli Telegram IDlari, vergul bilan. Botdagi `/id` o‘z IDingizni ko‘rsatadi. Username yozmang.
- `TEZCHECK_API_KEY`: Tezcheck savdogar kabinetida API kalit yarating.
- `TEZCHECK_CASH_DESK_CODE`: kabinetdagi kassa kodi; bu kassa IDsi emas.
- `TEZCHECK_BASE_URL`: namunadagi rasmiy HTTPS manzil.
- `DATABASE_PATH`: SQLite faylining joyi; `.env`dan nisbiy yo‘l berilsa loyiha papkasiga nisbatan olinadi.
- `POLL_INTERVAL`: tekshirishlar orasidagi soniyalar, kamida 30.
- `INVITE_TTL_HOURS`: kirish havolasi muddati, 1–168 soat.
- `CUSTOM_EMOJI_ID`: ixtiyoriy haqiqiy Telegram custom emoji IDsi. Bo‘sh bo‘lsa oddiy emoji ishlaydi. Telegram hisob/bot cheklovlariga qarab custom emoji oddiy emoji sifatida ko‘rinishi mumkin.

`.env` va SQLite faylini oshkor qilmang. Loglarda token, API javobi va to‘lov/taklif URLlari chiqarilmaydi.

Yopiq kanal yarating va botni **Invite users** huquqi bilan admin qiling. Kanalda ochiq username bo‘lmasin. Kanal postini botga yuborib chat ID aniqlash mumkin; ishonchli Telegram Bot API `getUpdates` javobida `channel_post.chat.id` qiymati ham ko‘rinadi. Polling ishlayotgan paytda ikkinchi `getUpdates` chaqiruvini ishlatmang. Kanal IDsi odatda `-100` bilan boshlanadi. Uni kurs qo‘shish suhbatida kiriting; bot kanal turi va o‘z huquqlarini tekshiradi.

Admin avval botni `/start` bilan ochsin. `/admin` menyusida kurs qo‘shish, barcha maydonlarni tahrirlash, faol/nofaol qilish, tasdiqlab o‘chirish, sahifalangan buyurtmalar va statistika bor. Tahrirlashda nom, tavsif, narx, kanal ketma-ket qayta kiritiladi. `/cancel` suhbatni bekor qiladi. Kurs kartasida alohida `https://t.me/BOT_USERNAME?start=course_ID` havolasi chiqadi. Oddiy xaridor admin menyusini ko‘rmaydi; admin handlerlari ham IDni tekshiradi.

## Tezcheck bilan shartnoma

Kod yozishdan oldin [rasmiy Merchant API hujjati](https://tezcheck.uz/api-docs) tekshirildi. Aiogram versiyasi [rasmiy 3.29 hujjatlariga](https://docs.aiogram.dev/en/v3.29.0/changelog.html) asoslangan.

API mijozi autentifikatsiya sarlavhalarini yuboradi. Hisob `POST /bills` bilan yaratiladi; `data.bill.id` va `data.payment_url` darhol saqlanadi. Narx butun so‘mda, API summasi esa 100 ga ko‘paytirilib yuboriladi. Har bir order o‘zgarmas UUID reference va idempotency kalitiga ega.

Holat uchun **POST** `/bills/{bill}` ishlatiladi. `bill.paid`, `bill.livemode`, haqiqiy muhit, payment holati, summa, valyuta va buyurtmaga moslik tekshiriladi. Test to‘lovi kanalni ochmaydi. Brauzer qaytishi va tugma bosilishi to‘lov isboti emas. Webhook kerak emas: foydalanuvchi tugmasi va fon tekshiruvi mavjud. `/health` admin buyrug‘i bazani va autentifikatsiyalangan `GET /me` ulanishini tekshiradi; hisob yaratmaydi.

Kassa kalitida faqat imzolangan so‘rovlar talabi yoqilgan bo‘lsa, bu Bearer mijozi rad etiladi. Ushbu loyiha uchun kabinetda Bearer so‘rovlariga ruxsat bering.

## Saqlash va xatolardan tiklash

Bir foydalanuvchi/kurs uchun bitta ochiq buyurtma va bitta xarid mavjud. To‘lovni qayta ishlash tranzaksiyali. Mavjud xarid uchun yangi hisob ochilmaydi; saqlangan havola qayta ko‘rsatilishi mumkin. Kurs o‘chirilishi arxivlash orqali bajariladi, buyurtma va audit tarixi saqlanadi. Buyurtma narxi, nomi va kanal IDsi xarid boshlangan paytdagi nusxasi bilan saqlanadi.

Noaniq hisob yaratish javobi o‘sha order va idempotency kaliti bilan qayta so‘raladi. Idempotency saqlanishi cheklanganligi sabab 23 soatdan eski, bill IDsi olinmagan order `review` holatiga o‘tadi. Admin kabinetda orderning `external_reference` raqami bo‘yicha solishtirishi kerak; avtomatik yangi hisob ochilmaydi.

Kirish havolasi muddati cheklangan **join request** havolasi: bot faqat to‘lagan foydalanuvchining IDsi uchun so‘rovni tasdiqlaydi, keyin havolani bekor qiladi. Telegramda join request va `member_limit=1` birga ishlatilmaydi. Ushbu usul ulashilgan havoladan begona shaxs kirishini cheklaydi. Boshqa adminlar begona so‘rovlarni qo‘lda tasdiqlamasin.

Telegramga taklif yaratish paytida jarayon uzilsa, yangi havola avtomatik yaratilmaydi: admin yordam beradi. Kanal huquqi xatosi haqida adminlarga xabar yuboriladi. Havola muddati o‘tgan yoki `error` holatidagi haqiqiy xaridorga admin kabinetdagi tekshiruvdan so‘ng qo‘lda yordam beradi. To‘langan foydalanuvchini qayta to‘lashga majburlamang. Bot bloklangan bo‘lsa xabar yetkazish keyingi fon aylanishida qayta uriniladi.

Faqat bitta bot jarayonini ishga tushiring. Buyurtmalar, xaridlar va audit doimiy; tugallanmagan admin suhbatlari xotirada, restartdan keyin qayta boshlanadi. SQLite joylashgan disk doimiy bo‘lsin. Ishlayotgan WAL bazasini nusxalashda SQLite backup usulidan foydalaning yoki botni to‘xtatib nusxalang. Bu bot refund/chargebackdan keyin kanal a’zosini avtomatik chiqarishni bajarmaydi; keyingi qaytarishlarni admin nazorat qiladi.

## Test

```bash
python -m unittest discover -s tests -v
```

Testlar bir vaqtdagi xarid, restartdan keyingi buyurtma, takroriy tasdiqlash, kurs o‘zgarishidan mustaqil order nusxasi, test to‘lovi va noto‘g‘ri summani rad etishni tekshiradi. Haqiqiy Telegram/Tezcheck tarmog‘iga murojaat qilmaydi.

Qo‘lda yakuniy ssenariy:

1. `/health` bilan kassa ulanishini tekshiring.
2. `/admin` → **Kurs qo‘shish** → nom → tavsif → narx → kanal ID → tasdiqlash.
3. Kurs kartasidagi deep linkni boshqa foydalanuvchi bilan oching.
4. **To‘lov qilish** → hisob yaratilishini tekshiring. Qayta bosilganda o‘sha hisob chiqsin.
5. To‘lamasdan **To‘lovni tekshirish** bosing: kanal havolasi berilmasin.
6. Avval Test provider bilan to‘lang: test to‘lovi sifatida rad etilsin. Bu haqiqiy kursni ochmaydi; jonli sinov uchun boshqa foydalanuvchi/kursdan foydalaning.
7. Ruxsat etilgan haqiqiy provayder bilan to‘lovni yakunlang. **To‘lovni tekshirish** yoki fon vazifasini kuting.
8. Kanal havolasini oching, kirish so‘rovi aynan xaridor uchun tasdiqlansin. Havolani boshqa ID bilan ochganda rad etilsin.
9. Botni qayta ishga tushiring va xaridni takrorlang: ikkinchi hisob/havola yaratilmasin. Admin statistikasida bitta to‘lov ko‘rinsin.

Ushbu tayyorlash muhitida Python runtime topilmadi; avtomatik testlar va real to‘lov yakunigacha ishga tushirish bajarilmagan. Ishga tushirish uchun haqiqiy tokenlar, kassa va kanal huquqlari kerak.

````
