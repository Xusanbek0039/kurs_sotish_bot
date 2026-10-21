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
from database import Database, now
from pymongo.errors import PyMongoError
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
        if isinstance(event.exception, PyMongoError):
            message = event.update.message or (event.update.callback_query.message if event.update.callback_query else None)
            if message and message.chat.type == 'private':
                with contextlib.suppress(TelegramAPIError):
                    await message.answer('Baza bilan aloqa vaqtincha uzildi. Keyinroq qayta urinib ko‘ring.')
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
        rows = await self.db.rows('courses', {'active': 1, 'deleted': 0}, sort=[('id', 1)])
        items = [(f'📚 {c["name"]} — {money(c["price"])}', f'u:course:{c["id"]}') for c in rows]
        if message.from_user.id in self.cfg.admins:
            items.append(('⚙️ Admin panel', 'a:menu'))
        emoji = f'<tg-emoji emoji-id="{self.cfg.emoji}">📚</tg-emoji>' if self.cfg.emoji else '📚'
        await message.answer(f'{emoji} Assalomu alaykum! Kursni tanlang.' if rows else 'Hozircha faol kurslar yo‘q.',
                             reply_markup=keyboard(items) if items else None)

    async def show_course(self, message, cid):
        c = await self.db.one('courses', {'id': cid, 'active': 1, 'deleted': 0})
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
                            await self.db.update('orders', {'id': o['id']}, {'status': 'review'})
                            await self.alert(o, 'Eski noaniq buyurtma: Tezcheck kabinetida external_reference bo‘yicha solishtiring.')
                            raise PaymentError('Eski hisob holati noaniq. Adminga murojaat qiling.')
                        data = await self.api.create(o)
                        await self.db.update('orders', {'id': o['id']}, {
                            'bill_id': data['bill']['id'], 'payment_url': data['payment_url'], 'status': 'pending'})
                        o = await self.db.one('orders', {'id': o['id']})
                    await call.message.answer(f'Buyurtma #{o["id"]}. Summa: {money(o["amount"])}\nTo‘lovdan so‘ng tekshirish tugmasini bosing.',
                        reply_markup=keyboard([('💳 To‘lov sahifasi', o['payment_url']), ('🔄 To‘lovni tekshirish', f'u:check:{o["id"]}')]))
                elif action == 'check':
                    o = await self.db.one('orders', {'id': ident, 'user_id': call.from_user.id})
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
        except PyMongoError:
            log.warning('MongoDB buyurtmani saqlash/tekshirish xatosi')
            await call.message.answer('Baza bilan aloqa vaqtincha uzildi. Keyinroq qayta tekshiring; qayta to‘lov qilmang.')

    async def verify(self, o):
        await self.db.update('orders', {'id': o['id']}, {'last_checked': now()})
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
        p = await self.db.one('purchases', {'order_id': o['id']})
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
            claim = await self.db.update('purchases', {'id': p['id'], 'status': 'waiting'}, {'status': 'creating'})
            if not claim.modified_count:
                return
            expiry = datetime.now(timezone.utc) + timedelta(hours=self.cfg.invite_hours)
            try:
                invite = await self.bot.create_chat_invite_link(o['channel_chat_id'],
                    name=f'order-{o["id"]}', expire_date=expiry, creates_join_request=True)
                await self.db.update('purchases', {'id': p['id']}, {
                    'invite_link': invite.invite_link, 'expires_at': int(expiry.timestamp()), 'status': 'ready'})
                p = await self.db.one('purchases', {'id': p['id']})
            except TelegramAPIError as exc:
                await self.db.update('purchases', {'id': p['id']}, {'status': 'error'})
                await self.alert(o, 'Kanal taklifini yaratib bo‘lmadi: ' + type(exc).__name__)
                with contextlib.suppress(TelegramAPIError):
                    await self.bot.send_message(o['user_id'], 'To‘lov tasdiqlandi. Kanalga kirish uchun adminga murojaat qiling. Buyurtma #' + str(o['id']))
                return
        if repeat or not p['notified']:
            await self.bot.send_message(o['user_id'], '✅ Kurs xarid qilingan. Havolani ochib kirish so‘rovini yuboring. Faqat sizning IDingiz tasdiqlanadi.',
                                        reply_markup=keyboard([('🔐 Kurs kanaliga kirish', p['invite_link'])]))
            await self.db.update('purchases', {'id': p['id']}, {'notified': 1})

    async def join(self, request):
        link = request.invite_link.invite_link if request.invite_link else None
        if not link:
            return
        p = await self.db.purchase_by_link(link)
        if not p:
            return
        if (p['user_id'] != request.from_user.id or p['channel_chat_id'] != request.chat.id or
                p['status'] != 'ready' or p['expires_at'] <= int(time.time())):
            await request.decline()
            return
        await request.approve()
        await self.db.update('purchases', {'id': p['id']}, {'status': 'joined'})
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
            rows = await self.db.rows('courses', {'deleted': 0}, sort=[('id', 1)], skip=page * 10, limit=10)
            items = [(f'#{c["id"]} {c["name"]} ({"faol" if c["active"] else "nofaol"})', f'a:course:{c["id"]}') for c in rows]
            if page:
                items.append(('⬅️ Oldingi', f'a:list:{page-1}'))
            if len(rows) == 10:
                items.append(('➡️ Keyingi', f'a:list:{page+1}'))
            await call.message.answer('Kurslar:', reply_markup=keyboard(items or [('Admin panel', 'a:menu')]))
        elif action == 'orders':
            page = max(0, int(bits[2]))
            rows = await self.db.orders_page(page)
            text = '\n'.join(f'#{o["id"]} | user {o["user_id"]} | kurs {o["course_id"]}\n{money(o["amount"])} | {o["status"]} | kirish: {o["access"] or "yo‘q"}\nref: {o["external_reference"]}' for o in rows)
            items = [('Admin panel', 'a:menu')]
            if page:
                items.append(('⬅️ Oldingi', f'a:orders:{page-1}'))
            if len(rows) == 10:
                items.append(('➡️ Keyingi', f'a:orders:{page+1}'))
            await call.message.answer(escape(text or 'Buyurtmalar yo‘q.'), reply_markup=keyboard(items))
        elif action == 'stats':
            o = await self.db.stats()
            await call.message.answer(f'Kurslar: {o["courses"]}\nBuyurtmalar: {o["n"]}\nMuvaffaqiyatli to‘lovlar: {o["paid"]}\nTushum (yalpi): {money(o["revenue"])}')
        elif action == 'save':
            if await state.get_state() != CourseForm.confirm.state:
                await call.message.answer('Kiritish muddati tugagan. /admin orqali qayta boshlang.')
                return
            d = await state.get_data()
            if d.get('edit_id'):
                await self.db.course_change(uid, 'edit', {'name': d['name'], 'description': d['description'],
                    'price': d['price'], 'channel_chat_id': d['channel']}, cid=d['edit_id'])
            else:
                await self.db.course_change(uid, 'add', {'name': d['name'], 'description': d['description'],
                    'price': d['price'], 'channel_chat_id': d['channel']})
            await state.clear()
            await call.message.answer('✅ Kurs saqlandi.')
        elif len(bits) >= 3:
            cid = int(bits[2])
            c = await self.db.one('courses', {'id': cid, 'deleted': 0})
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
                await self.db.course_change(uid, 'toggle', cid=cid)
                await call.message.answer('Kurs holati o‘zgartirildi.')
            elif action == 'delete':
                await call.message.answer('Kursni o‘chirishni tasdiqlaysizmi? Xaridlar tarixi saqlanadi.',
                                          reply_markup=keyboard([('Ha, o‘chirish', f'a:remove:{cid}'), ('Bekor qilish', f'a:course:{cid}')]))
            elif action == 'remove':
                await self.db.course_change(uid, 'delete', cid=cid)
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
        try:
            await self.db.ping()
            await self.db.audit(message.from_user.id, 'health', {})
        except PyMongoError:
            await message.answer('MongoDB Atlas: ❌ ulanishni va Network Access sozlamalarini tekshiring.')
            return
        try:
            data = await self.api.request('GET', '/me')
            desk = data.get('cash_desk')
            ok = isinstance(desk, dict) and str(desk.get('code')) == self.cfg.desk and desk.get('currency') == 'UZS'
            await message.answer('MongoDB Atlas: ✅\nTelegram: ✅\nTezcheck: ' + ('✅ kalit va kassa mos.' if ok else '⚠️ kassa sozlamalarini tekshiring.'))
        except PaymentError as exc:
            await message.answer('MongoDB Atlas: ✅\nTelegram: ✅\n' + escape(str(exc)))

    async def poll(self):
        while True:
            try:
                rows = await self.db.rows('orders', {'status': {'$in': ['pending', 'paid']}}, sort=[('last_checked', 1)], limit=30)
                for o in rows:
                    try:
                        async with self.payment_lock:
                            if o['status'] == 'pending':
                                if await self.verify(o):
                                    await self.deliver(o)
                            else:
                                await self.db.update('orders', {'id': o['id']}, {'last_checked': now()})
                                await self.deliver(o)
                    except (PaymentError, TelegramAPIError) as exc:
                        log.warning('Tekshirish xatosi: buyurtma=%s tur=%s', o['id'], type(exc).__name__)
                    await asyncio.sleep(1)
            except Exception as exc:
                log.error('Fon vazifasi: %s', type(exc).__name__)
            await asyncio.sleep(self.cfg.interval)


async def main():
    cfg = Config.load()
    db = Database(cfg.mongo_uri, cfg.database)
    await db.open()
    bot = Bot(cfg.token, default=DefaultBotProperties(parse_mode='HTML'))
    api = Tezcheck(cfg)
    task = None
    try:
        # Interrupted invite creation is intentionally handled by an admin, never recreated blindly.
        await db.update('purchases', {'status': 'creating'}, {'status': 'error'}, many=True)
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
    except PyMongoError:
        print('MongoDB Atlas bilan ulanish/amaliyot bajarilmadi. URI, ruxsatlar va Network Access sozlamalarini tekshiring.')

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
    mongo_uri: str
    interval: int
    invite_hours: int
    emoji: str

    @classmethod
    def load(cls):
        load_dotenv(ROOT / '.env')
        required = ['BOT_TOKEN', 'ADMIN_IDS', 'TEZCHECK_API_KEY', 'TEZCHECK_CASH_DESK_CODE', 'MONGODB_URI']
        if any(not os.getenv(k, '').strip() for k in required):
            raise ValueError('.env ichida BOT_TOKEN, ADMIN_IDS, MONGODB_URI va Tezcheck sozlamalarini to‘ldiring.')
        mongo_uri = os.environ['MONGODB_URI'].strip()
        if not mongo_uri.startswith(('mongodb+srv://', 'mongodb://')):
            raise ValueError('MONGODB_URI MongoDB ulanish manzili bo‘lishi kerak.')
        if '<' in mongo_uri or '>' in mongo_uri:
            raise ValueError('MONGODB_URI ichidagi <db_username>/<db_password> o‘rniga haqiqiy ma’lumotlarni yozing.')
        database = os.getenv('MONGODB_DATABASE', 'kurs_sotish_bot').strip()
        if not database or any(c in database for c in '/\\. "$*<>:|?'):
            raise ValueError('MONGODB_DATABASE nomi noto‘g‘ri.')
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
                   database, mongo_uri, interval, hours, emoji)

````

## database.py
````text
import uuid
from datetime import datetime, timezone

from pymongo import AsyncMongoClient, ReturnDocument
from pymongo.errors import DuplicateKeyError
from pymongo.read_concern import ReadConcern
from pymongo.write_concern import WriteConcern


def now():
    return datetime.now(timezone.utc).replace(tzinfo=None).isoformat(sep=' ', timespec='seconds')


class Database:
    def __init__(self, uri, name):
        self.uri, self.name = uri, name

    async def open(self):
        self.client = AsyncMongoClient(self.uri, serverSelectionTimeoutMS=10000,
                                       connectTimeoutMS=10000, socketTimeoutMS=20000)
        self.db = self.client.get_database(self.name, write_concern=WriteConcern('majority'))
        try:
            await self.ping()
            for collection in ('courses', 'orders', 'purchases', 'admin_logs'):
                await self.db[collection].create_index('id', unique=True)
            await self.db.orders.create_index([('user_id', 1), ('course_id', 1)], unique=True)
            await self.db.orders.create_index('external_reference', unique=True)
            for field in ('bill_id', 'payment_id'):
                await self.db.orders.create_index(field, unique=True,
                    partialFilterExpression={field: {'$type': 'string'}})
            await self.db.orders.create_index([('status', 1), ('last_checked', 1)])
            await self.db.courses.create_index([('deleted', 1), ('active', 1), ('id', 1)])
            await self.db.purchases.create_index([('user_id', 1), ('course_id', 1)], unique=True)
            await self.db.purchases.create_index('order_id', unique=True)
            await self.db.purchases.create_index('invite_link', unique=True,
                partialFilterExpression={'invite_link': {'$type': 'string'}})
            await self.db.admin_logs.create_index('created_at')
        except BaseException:
            await self.client.close()
            raise

    async def close(self):
        await self.client.close()

    async def ping(self):
        await self.client.admin.command('ping')

    async def next_id(self, collection):
        counter = await self.db.counters.find_one_and_update({'_id': collection},
            {'$inc': {'value': 1}}, upsert=True, return_document=ReturnDocument.AFTER)
        return counter['value']

    async def one(self, collection, query):
        return await self.db[collection].find_one(query, {'_id': 0})

    async def rows(self, collection, query=None, sort=None, skip=0, limit=0):
        cursor = self.db[collection].find(query or {}, {'_id': 0})
        if sort:
            cursor = cursor.sort(sort)
        if skip:
            cursor = cursor.skip(skip)
        if limit:
            cursor = cursor.limit(limit)
        return [row async for row in cursor]

    async def update(self, collection, query, values, many=False):
        method = self.db[collection].update_many if many else self.db[collection].update_one
        return await method(query, {'$set': values})

    async def audit(self, admin, action, details):
        await self.db.admin_logs.insert_one({'id': await self.next_id('admin_logs'),
            'admin_id': admin, 'action': action, 'details': details, 'created_at': now()})

    async def transaction(self, callback):
        async with self.client.start_session() as session:
            return await session.with_transaction(callback, read_concern=ReadConcern('snapshot'),
                                                  write_concern=WriteConcern('majority'))

    async def course_change(self, admin, action, values=None, cid=None):
        values = values or {}
        ident = await self.next_id('courses') if action == 'add' else cid
        log_id = await self.next_id('admin_logs')

        async def change(session):
            if action == 'add':
                await self.db.courses.insert_one({'id': ident, **values, 'active': 1,
                    'deleted': 0, 'created_at': now()}, session=session)
            else:
                query = {'id': ident, 'deleted': 0}
                if action == 'toggle':
                    result = await self.db.courses.update_one(query,
                        [{'$set': {'active': {'$subtract': [1, '$active']}}}], session=session)
                else:
                    data = {'deleted': 1, 'active': 0} if action == 'delete' else values
                    result = await self.db.courses.update_one(query, {'$set': data}, session=session)
                if not result.matched_count:
                    raise ValueError('Kurs topilmadi.')
            await self.db.admin_logs.insert_one({'id': log_id, 'admin_id': admin,
                'action': action, 'details': {'course_id': ident, 'values': values},
                'created_at': now()}, session=session)
            return ident

        return await self.transaction(change)

    async def order(self, user, course):
        query = {'user_id': user, 'course_id': course}
        existing = await self.one('orders', query)
        if existing:
            return existing
        c = await self.one('courses', {'id': course, 'active': 1, 'deleted': 0})
        if not c:
            raise ValueError('Kurs hozir sotuvda emas.')
        order = {'id': await self.next_id('orders'), **query, 'amount': c['price'],
            'channel_chat_id': c['channel_chat_id'], 'title': c['name'],
            'external_reference': 'ORD-' + uuid.uuid4().hex, 'status': 'creating',
            'bill_id': None, 'payment_url': None, 'payment_id': None,
            'created_at': now(), 'paid_at': None, 'last_checked': ''}
        try:
            await self.db.orders.insert_one(order)
        except DuplicateKeyError:
            existing = await self.one('orders', query)
            if not existing:
                raise
            return existing
        order.pop('_id', None)
        return order

    async def mark_paid(self, order, payment_id):
        purchase_id = await self.next_id('purchases')

        async def paid(session):
            current = await self.db.orders.find_one({'id': order['id']}, session=session)
            if not current or current['user_id'] != order['user_id'] or current['course_id'] != order['course_id']:
                raise ValueError('Buyurtma topilmadi.')
            if current.get('payment_id') and current['payment_id'] != payment_id:
                raise ValueError('Buyurtmaning to‘lov IDsi o‘zgardi.')
            await self.db.orders.update_one({'id': order['id']}, {'$set': {
                'status': 'paid', 'payment_id': payment_id, 'paid_at': current.get('paid_at') or now()}}, session=session)
            await self.db.purchases.update_one({'order_id': order['id']}, {'$setOnInsert': {
                'id': purchase_id, 'user_id': order['user_id'], 'course_id': order['course_id'],
                'order_id': order['id'], 'invite_link': None, 'status': 'waiting',
                'expires_at': None, 'notified': 0, 'created_at': now()}}, upsert=True, session=session)

        await self.transaction(paid)

    async def purchase_by_link(self, link):
        p = await self.one('purchases', {'invite_link': link})
        if p:
            o = await self.one('orders', {'id': p['order_id'], 'status': 'paid'})
            if o:
                return {**p, 'channel_chat_id': o['channel_chat_id']}
        return None

    async def orders_page(self, page):
        rows = await self.rows('orders', sort=[('id', -1)], skip=page * 10, limit=10)
        for order in rows:
            p = await self.one('purchases', {'order_id': order['id']})
            order['access'] = p['status'] if p else None
        return rows

    async def stats(self):
        courses = await self.db.courses.count_documents({'deleted': 0})
        count = await self.db.orders.count_documents({})
        result = [x async for x in await self.db.orders.aggregate([
            {'$match': {'status': 'paid'}}, {'$group': {'_id': None,
                'paid': {'$sum': 1}, 'revenue': {'$sum': '$amount'}}}])]
        return {'courses': courses, 'n': count, 'paid': result[0]['paid'] if result else 0,
                'revenue': result[0]['revenue'] if result else 0}

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
pymongo==4.13.2
dnspython==2.7.0
python-dotenv==1.1.1

````

## .env.example
````text
BOT_TOKEN=
ADMIN_IDS=123456789
TEZCHECK_API_KEY=
TEZCHECK_CASH_DESK_CODE=
TEZCHECK_BASE_URL=https://api.tezcheck.uz/api/merchant/v1
MONGODB_URI=
MONGODB_DATABASE=kurs_sotish_bot
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
import os
import unittest
import uuid

from pymongo.errors import DuplicateKeyError

from database import Database
from tezcheck import PaymentError, Tezcheck


class PaymentValidationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.order = {'id': 1, 'amount': 150000, 'external_reference': 'ORD-test', 'bill_id': 'link_1'}
        self.bill = {'id': 'link_1', 'external_reference': 'ORD-test', 'amount_minor': 15000000,
                     'currency': 'UZS', 'paid': True, 'livemode': True, 'environment': 'live', 'state': 'expired'}
        self.payment = {'id': 'pay_1', 'state': 'succeeded', 'amount_minor': 15000000, 'currency': 'UZS',
                        'livemode': True, 'environment': 'live', 'refunded_amount_minor': 0}
        self.client = object.__new__(Tezcheck)

        async def response(*args, **kwargs):
            return {'bill': self.bill, 'payment': self.payment}

        self.client.request = response

    async def test_live_payment_passes_even_if_bill_expired(self):
        self.assertEqual(await self.client.check(self.order), (True, 'pay_1'))

    async def test_unpaid_never_passes(self):
        self.bill['paid'] = False
        self.assertEqual(await self.client.check(self.order), (False, None))

    async def test_test_payment_is_rejected(self):
        self.bill['livemode'] = False
        with self.assertRaises(PaymentError):
            await self.client.check(self.order)

    async def test_wrong_amount_is_rejected(self):
        self.payment['amount_minor'] = 1
        with self.assertRaises(PaymentError):
            await self.client.check(self.order)

    async def test_wrong_reference_is_rejected(self):
        self.bill['external_reference'] = 'OTHER'
        with self.assertRaises(PaymentError):
            await self.client.check(self.order)


@unittest.skipUnless(os.getenv('MONGODB_TEST_URI'), 'MONGODB_TEST_URI berilmagan: Atlas integratsiya testlari o‘tkazib yuborildi.')
class MongoDBTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        # Never use the configured production database. Each test owns a new random test DB.
        self.db = Database(os.environ['MONGODB_TEST_URI'], 'kurs_bot_test_' + uuid.uuid4().hex)
        await self.db.open()
        self.addAsyncCleanup(self.cleanup_database)
        self.cid = await self.db.course_change(1, 'add', {
            'name': 'Python', 'description': 'Darslar', 'price': 150000, 'channel_chat_id': -1001234567890})

    async def cleanup_database(self):
        try:
            await self.db.client.drop_database(self.db.name)
        finally:
            await self.db.close()

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
        self.assertEqual(len(await self.db.rows('purchases')), 1)
        self.assertEqual((await self.db.order(42, self.cid))['status'], 'paid')
        await self.db.update('purchases', {'order_id': o['id']}, {'invite_link': 'https://t.me/+saved', 'status': 'ready'})
        await self.db.mark_paid(o, 'pay_unique')
        self.assertEqual((await self.db.one('purchases', {'order_id': o['id']}))['invite_link'], 'https://t.me/+saved')

    async def test_duplicate_payment_rolls_back(self):
        first = await self.db.order(42, self.cid)
        second = await self.db.order(43, self.cid)
        await self.db.mark_paid(first, 'pay_unique')
        with self.assertRaises(DuplicateKeyError):
            await self.db.mark_paid(second, 'pay_unique')
        self.assertEqual((await self.db.one('orders', {'id': second['id']}))['status'], 'creating')
        self.assertIsNone(await self.db.one('purchases', {'order_id': second['id']}))

    async def test_purchase_failure_rolls_back_paid_status(self):
        o = await self.db.order(42, self.cid)
        await self.db.db.purchases.insert_one({'id': 1000, 'user_id': 42, 'course_id': self.cid, 'order_id': -1})
        with self.assertRaises(DuplicateKeyError):
            await self.db.mark_paid(o, 'pay_unique')
        self.assertEqual((await self.db.one('orders', {'id': o['id']}))['status'], 'creating')

    async def test_order_snapshots_survive_course_edit_and_delete(self):
        o = await self.db.order(42, self.cid)
        await self.db.course_change(1, 'edit', {'price': 200000, 'channel_chat_id': -100999}, cid=self.cid)
        await self.db.course_change(1, 'delete', cid=self.cid)
        same = await self.db.order(42, self.cid)
        self.assertEqual(same['amount'], 150000)
        self.assertEqual(same['channel_chat_id'], o['channel_chat_id'])
        self.assertEqual(len(await self.db.rows('admin_logs')), 3)

    async def test_invite_claim_is_atomic(self):
        o = await self.db.order(42, self.cid)
        await self.db.mark_paid(o, 'pay_unique')
        results = await asyncio.gather(*(self.db.update('purchases',
            {'order_id': o['id'], 'status': 'waiting'}, {'status': 'creating'}) for _ in range(10)))
        self.assertEqual(sum(result.modified_count for result in results), 1)


if __name__ == '__main__':
    unittest.main()

````

## README.md
````text
# Telegram orqali kurs sotish

Python 3.11+ va MongoDB Atlas asosidagi aiogram 3 bot. Boshlash buyrug‘i: `python main.py`.

## Tuzilishi

```text
kurs_sotish_bot/
  main.py                  Telegram menyulari, admin suhbatlari va fon vazifasi
  config.py                .env konfiguratsiyasi
  database.py              MongoDB kolleksiyalari, indekslar va tranzaksiyalar
  tezcheck.py               Merchant API mijozi
  requirements.txt
  .env.example
  .gitignore
  README.md
  tests/test_payments.py
  FULL_CODE.md             barcha fayllar kodining birlashtirilgan nusxasi
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
- `MONGODB_URI`: Atlas kabinetidagi Connect → Drivers bo‘limidan olingan maxfiy ulanish manzili.
- `MONGODB_DATABASE`: baza nomi, odatda `kurs_sotish_bot`.
- `POLL_INTERVAL`: tekshirishlar orasidagi soniyalar, kamida 30.
- `INVITE_TTL_HOURS`: kirish havolasi muddati, 1–168 soat.
- `CUSTOM_EMOJI_ID`: ixtiyoriy haqiqiy Telegram custom emoji IDsi. Bo‘sh bo‘lsa oddiy emoji ishlaydi. Telegram hisob/bot cheklovlariga qarab custom emoji oddiy emoji sifatida ko‘rinishi mumkin.

`.env` va MongoDB ulanish manzilini oshkor qilmang. Loglarda token, MongoDB URI, API javobi va to‘lov/taklif URLlari chiqarilmaydi.

## MongoDB Atlas sozlash

1. [MongoDB Atlas](https://www.mongodb.com/atlas) hisobingizda loyiha va klaster yarating.
2. Database Access orqali baza foydalanuvchisini yarating. Bot uchun `kurs_sotish_bot` bazasida `readWrite` huquqini bering.
3. Network Access bo‘limida bot ishlaydigan kompyuter/serverning tashqi IP manzilini ruxsat ro‘yxatiga qo‘shing. [Atlas IP access list hujjati](https://www.mongodb.com/docs/atlas/security/add-ip-address-to-list/).
4. Connect → Drivers → Python orqali connection stringni oling. Username va parolni o‘zingizniki bilan almashtiring. Paroldagi maxsus belgilar URI uchun percent-encode qilinishi kerak.
5. `.env`ga yozing. Quyidagilar faqat format namunasi, haqiqiy kalitlar emas:

```dotenv
MONGODB_URI="mongodb+srv://BOT_USER:ENCODED_PASSWORD@YOUR_CLUSTER.mongodb.net/?retryWrites=true&w=majority"
MONGODB_DATABASE=kurs_sotish_bot
```

Bot bazani, `courses`, `orders`, `purchases`, `admin_logs` va raqamli IDlar uchun `counters` kolleksiyasini avtomatik yaratadi. Ulanish uchun internet zarur. PyMongo Async ishlatiladi; [rasmiy tranzaksiya hujjati](https://www.mongodb.com/docs/languages/python/pymongo-driver/current/crud/transactions/). Tranzaksiyalar uchun replica set kerak; Atlas klasteridan foydalaning, standalone lokal MongoDB mos emas. Eski SQLite fayli bo‘lsa, uning ma’lumotlari avtomatik import qilinmaydi.

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

Faqat bitta bot jarayonini ishga tushiring. Buyurtmalar, xaridlar va audit Atlasda doimiy saqlanadi; tugallanmagan admin suhbatlari xotirada, restartdan keyin qayta boshlanadi. Bir user/kurs uchun bitta order saqlanadi va qayta ishlatiladi. To‘lov va xarid yozuvlari, shuningdek kurs o‘zgarishi va uning auditi bir tranzaksiyada yoziladi. Noyob indekslar bill, payment, xarid va havola takrorlanishini cheklaydi. Taklif yaratish huquqi atomar yangilash bilan olinadi. Bu bot refund/chargebackdan keyin kanal a’zosini avtomatik chiqarishni bajarmaydi; keyingi qaytarishlarni admin nazorat qiladi.

## Test

```bash
python -m unittest discover -s tests -v
```

API validatsiya testlari tarmoqsiz ishlaydi. Baza testlari `MONGODB_TEST_URI` berilmasa o‘tkazib yuboriladi. Atlas integratsiya testlari bir vaqtdagi xarid, restart, takroriy to‘lov, tranzaksiya rollback, kurs nusxasi, audit va taklif uchun atomar himoyani tekshiradi. Telegram/Tezcheck tarmog‘iga murojaat qilinmaydi.

Integratsiya testlari uchun alohida test klasterida `kurs_bot_test_` prefiksli vaqtinchalik bazalarni yaratish/o‘chirish huquqi kerak. Har bir test noyob vaqtinchalik baza yaratadi va tugagach o‘chiradi. Test URI oddiy `.env`dan avtomatik o‘qilmaydi; muhit o‘zgaruvchisi qilib bering:

```powershell
$env:MONGODB_TEST_URI = 'ATLAS_TEST_CONNECTION_STRING'
python -m unittest discover -s tests -v
```

```bash
export MONGODB_TEST_URI='ATLAS_TEST_CONNECTION_STRING'
python -m unittest discover -s tests -v
```

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
