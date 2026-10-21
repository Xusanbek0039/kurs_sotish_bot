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
