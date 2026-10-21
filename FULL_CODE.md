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
from marketing import (MARKETING_FIELDS, ORDER_LABELS, course_card, course_details,
                       course_link, money, share_link, validate_marketing)

log = logging.getLogger('courses')


def keyboard(items):
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text=text, **({'url': value} if value.startswith('https://') else {'callback_data': value}))
    ] for text, value in items])


class CourseForm(StatesGroup):
    name = State()
    description = State()
    price = State()
    channel = State()
    confirm = State()


class MarketingForm(StatesGroup):
    value = State()


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
        r.message.register(self.catalog_command, Command('courses'))
        r.message.register(self.purchases_command, Command('purchases'))
        r.message.register(self.help_command, Command('help'))
        r.callback_query.register(self.admin_callback, F.data.startswith('a:'))
        r.callback_query.register(self.customer_callback, F.data.startswith('u:'))
        r.message.register(self.form, CourseForm.name)
        r.message.register(self.form, CourseForm.description)
        r.message.register(self.form, CourseForm.price)
        r.message.register(self.form, CourseForm.channel)
        r.message.register(self.form, CourseForm.confirm)
        r.message.register(self.marketing_form, MarketingForm.value)
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
        await self.track('start', message.from_user.id)
        if len(parts) == 2 and parts[1].startswith('course_'):
            try:
                await self.show_course(message, int(parts[1][7:]), message.from_user.id)
            except ValueError:
                await message.answer('Kurs havolasi noto‘g‘ri.')
            return
        await self.catalog(message, message.from_user.id, greeting=True)

    async def track(self, event, uid, cid=0):
        if uid in self.cfg.admins:
            return
        try:
            await self.db.track(event, uid, cid)
        except PyMongoError:
            log.warning('Marketing statistikasi vaqtincha yozilmadi.')

    def navigation(self):
        return [('📚 Barcha kurslar', 'u:catalog:0'), ('🎓 Mening xaridlarim', 'u:mine:0'),
                ('❓ Savollar va yordam', 'u:help:0')]

    def support_items(self):
        if self.cfg.support_username:
            return [('💬 Maslahatchiga yozish', 'https://t.me/' + self.cfg.support_username)]
        return []

    async def catalog_command(self, message):
        if message.chat.type == 'private':
            await self.catalog(message, message.from_user.id)

    async def purchases_command(self, message):
        if message.chat.type == 'private':
            await self.my_purchases(message, message.from_user.id)

    async def help_command(self, message):
        if message.chat.type == 'private':
            await self.help(message, message.from_user.id)

    async def catalog(self, message, uid, page=0, greeting=False):
        page = max(0, page)
        rows = await self.db.rows('courses', {'active': 1, 'deleted': 0}, sort=[('id', 1)], skip=page * 8, limit=9)
        items = [(f'📚 {c["name"]} — {money(c["price"])}', f'u:course:{c["id"]}') for c in rows[:8]]
        if page:
            items.append(('⬅️ Oldingi kurslar', f'u:catalog:{page-1}'))
        if len(rows) > 8:
            items.append(('➡️ Keyingi kurslar', f'u:catalog:{page+1}'))
        items.extend(self.navigation()[1:])
        if uid in self.cfg.admins:
            items.append(('⚙️ Admin panel', 'a:menu'))
        emoji = f'<tg-emoji emoji-id="{self.cfg.emoji}">📚</tg-emoji>' if self.cfg.emoji else '📚'
        intro = f'{emoji} <b>{escape(self.cfg.brand)}</b>\n\n'
        if greeting:
            intro += 'Assalomu alaykum!\n' + escape(self.cfg.welcome) + '\n\n'
        intro += ('👇 Qiziqqan kursingizni oching: dastur va narxni ko‘rib chiqing.' if rows
                  else 'Hozir bu sahifada faol kurslar yo‘q. Savolingiz bo‘lsa, yordam bo‘limiga o‘ting.')
        await message.answer(intro, reply_markup=keyboard(items))

    async def help(self, message, uid):
        await message.answer(
            '<b>Xarid qanday ishlaydi?</b>\n\n'
            '1. Kurs sahifasida dastur va narx bilan tanishing.\n'
            '2. «Kursni xarid qilish» tugmasini bosing va Tezcheck sahifasida to‘lang.\n'
            '3. Bot to‘lov tasdig‘ini tekshiradi va yopiq kanalga kirish havolasini beradi.\n\n'
            '<b>To‘ladim, havola kelmadimi?</b>\n'
            '«Mening xaridlarim» → buyurtma → «To‘lovni tekshirish»ni bosing. '
            'Tasdiq kutilayotgan bo‘lsa, qayta pul to‘lamang.\n\n'
            '<b>Kursni avval sotib olganmisiz?</b>\n'
            'Xaridingiz saqlanadi. Mavjud buyurtmani ochib davom eting.\n\n'
            f'Yordam so‘raganda Telegram IDingizni yuboring: <code>{uid}</code>. '
            'Buyurtma raqami ham muammoni tezroq topishga yordam beradi.',
            reply_markup=keyboard(self.support_items() + self.navigation()[:2]))

    async def my_purchases(self, message, uid, page=0):
        page = max(0, page)
        orders = await self.db.rows('orders', {'user_id': uid}, sort=[('id', -1)], skip=page * 8, limit=9)
        items = [(f'{"✅" if o["status"] == "paid" else "🧾"} #{o["id"]} {o["title"][:60]}', f'u:order:{o["id"]}') for o in orders[:8]]
        if page:
            items.append(('⬅️ Oldingi xaridlar', f'u:mine:{page-1}'))
        if len(orders) > 8:
            items.append(('➡️ Keyingi xaridlar', f'u:mine:{page+1}'))
        items.extend(self.navigation()[::2])
        await message.answer('<b>Mening xaridlarim</b>\n\n' + (
            'Kursga kirish yoki to‘lov holatini ko‘rish uchun buyurtmani tanlang.' if orders
            else 'Hali xarid boshlamagansiz. Kurslarni ko‘rib, o‘zingizga mosini tanlang.'), reply_markup=keyboard(items))

    async def show_order(self, message, uid, oid):
        o = await self.db.one('orders', {'id': oid, 'user_id': uid})
        if not o:
            await message.answer('Buyurtma topilmadi.', reply_markup=keyboard(self.navigation()))
            return
        if o['status'] == 'paid':
            await self.deliver(o, repeat=True)
            return
        items = []
        if o.get('bill_id') and o.get('payment_url'):
            items.extend([('💳 To‘lovni davom ettirish', o['payment_url']), ('🔄 To‘lovni tekshirish', f'u:check:{oid}')])
        elif o['status'] == 'creating':
            items.append(('💳 Hisobni davom ettirish', f'u:buy:{o["course_id"]}'))
        items.extend(self.support_items() + self.navigation())
        await message.answer(f'<b>Buyurtma #{oid}</b>\n{escape(o["title"])}\n'
            f'Summa: {money(o["amount"])}\nHolat: {ORDER_LABELS.get(o["status"], "Tekshirilmoqda")}\n\n'
            'To‘lov qilgan bo‘lsangiz, yana pul yubormasdan holatni tekshiring.', reply_markup=keyboard(items))

    async def show_course(self, message, cid, uid=None):
        c = await self.db.one('courses', {'id': cid, 'active': 1, 'deleted': 0})
        if not c:
            await message.answer('Kurs hozir sotuvda emas. Oldingi xaridingizni «Mening xaridlarim»dan topasiz.', reply_markup=keyboard(self.navigation()))
            return
        if uid is not None:
            await self.track('course_view', uid, cid)
        existing = await self.db.one('orders', {'user_id': uid, 'course_id': cid}) if uid is not None else None
        cta = ('🎓 Xarid qilingan kursni ochish' if existing and existing['status'] == 'paid'
               else '💳 To‘lovni davom ettirish' if existing else '🚀 Kursni xarid qilish')
        items = [(cta, f'u:order:{existing["id"]}' if existing else f'u:buy:{cid}'),
                 ('📖 Dastur va batafsil ma’lumot', f'u:details:{cid}')]
        if c.get('preview_url'):
            items.append(('🎬 Bepul tanishuv darsi', c['preview_url']))
        items.extend(self.support_items() + [('📤 Kursni ulashish', share_link(self.username, c))] + self.navigation())
        await message.answer(course_card(c), reply_markup=keyboard(items))

    async def customer_callback(self, call):
        await call.answer()
        if call.message.chat.type != 'private':
            return
        try:
            _, action, raw = call.data.split(':')
            ident = int(raw)
            if action == 'catalog':
                await self.catalog(call.message, call.from_user.id, ident)
                return
            if action == 'mine':
                await self.my_purchases(call.message, call.from_user.id, ident)
                return
            if action == 'help':
                await self.help(call.message, call.from_user.id)
                return
            if action == 'course':
                await self.show_course(call.message, ident, call.from_user.id)
                return
            if action == 'details':
                c = await self.db.one('courses', {'id': ident, 'active': 1, 'deleted': 0})
                if not c:
                    await call.message.answer('Kurs hozir sotuvda emas.', reply_markup=keyboard(self.navigation()))
                    return
                await call.message.answer(course_details(c), reply_markup=keyboard([
                    ('🚀 Kurs sahifasiga qaytish', f'u:course:{ident}')] + self.support_items()))
                return
            async with self.payment_lock:
                if action == 'order':
                    await self.show_order(call.message, call.from_user.id, ident)
                    return
                if action == 'buy':
                    await self.track('checkout', call.from_user.id, ident)
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
                        await call.message.answer('⏳ To‘lov hisobi tayyorlanmoqda. Bir oz kuting.')
                        data = await self.api.create(o)
                        await self.db.update('orders', {'id': o['id']}, {
                            'bill_id': data['bill']['id'], 'payment_url': data['payment_url'], 'status': 'pending'})
                        o = await self.db.one('orders', {'id': o['id']})
                    await call.message.answer(f'<b>Yana bir qadam — kursga kirish</b>\n\n'
                        f'📚 {escape(o["title"])}\n🧾 Buyurtma #{o["id"]}\n💳 Summa: <b>{money(o["amount"])}</b>\n\n'
                        '1. To‘lov sahifasini oching.\n2. To‘lovni yakunlang.\n3. Botga qaytib «To‘lovni tekshirish»ni bosing.\n\n'
                        'Tasdiqdan keyin shu kursning yopiq kanaliga kirish havolasi beriladi. '
                        'To‘lovni tugatgan bo‘lsangiz, qayta to‘lamang.',
                        reply_markup=keyboard([('💳 Tezcheck orqali to‘lash', o['payment_url']),
                            ('🔄 To‘lovni tekshirish', f'u:check:{o["id"]}')] + self.support_items() + [('🎓 Mening xaridlarim', 'u:mine:0')]))
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
            await call.message.answer(escape(str(exc)), reply_markup=keyboard(
                self.support_items() + [('🎓 Mening xaridlarim', 'u:mine:0'), ('❓ Yordam', 'u:help:0')]))
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
                channel = str(o['channel_chat_id'])
                items = [('📚 Kurs kanalini ochish', 'https://t.me/c/' + channel[4:] + '/1')] if channel.startswith('-100') else []
                await self.bot.send_message(o['user_id'], f'🎓 <b>{escape(o["title"])}</b>\n\n'
                    'Ushbu kurs sizniki — xaridingiz saqlangan. Kanalni ochib o‘rganishni davom ettiring. '
                    'Kanaldan chiqib ketgan bo‘lsangiz, yordam bo‘limiga murojaat qiling.',
                    reply_markup=keyboard(items + self.support_items() + self.navigation()))
            return
        if p['status'] in ('creating', 'error'):
            if repeat:
                await self.bot.send_message(o['user_id'], f'To‘lov tasdiqlangan. Buyurtma #{o["id"]}. Kanalga kirish uchun yordam bo‘limiga yozing.',
                    reply_markup=keyboard(self.support_items() + [('❓ Yordam', 'u:help:0')]))
            return
        if p['invite_link'] and p['expires_at'] <= int(time.time()):
            if repeat:
                await self.bot.send_message(o['user_id'], f'Xaridingiz saqlangan. Havola muddati tugagan; buyurtma #{o["id"]} bilan yordam bo‘limiga yozing.',
                    reply_markup=keyboard(self.support_items() + [('❓ Yordam', 'u:help:0')]))
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
            await self.bot.send_message(o['user_id'], f'🎉 <b>Xaridingiz tasdiqlandi!</b>\n\n'
                f'📚 {escape(o["title"])}\n🧾 Buyurtma #{o["id"]}\n\n'
                'Keyingi qadam: quyidagi tugmani bosing va kanalga kirish so‘rovini yuboring. '
                'Bot aynan sizning so‘rovingizni tasdiqlaydi.\n\n'
                f'Havola muddati (UTC): {datetime.fromtimestamp(p["expires_at"], timezone.utc).strftime("%d.%m.%Y %H:%M")}. '
                'Xaridingizni «Mening xaridlarim» bo‘limidan ham topasiz.',
                reply_markup=keyboard([('🔐 O‘rganishni boshlash', p['invite_link'])] + self.support_items() + [('🎓 Mening xaridlarim', 'u:mine:0')]))
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
            ('🧾 Buyurtmalar va to‘lovlar', 'a:orders:0'), ('📊 Statistika', 'a:stats'),
            ('📈 Sotuv bosqichlari', 'a:funnel'), ('🛍 Xaridor ko‘rinishi', 'u:catalog:0')]))

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
        elif action == 'funnel':
            stages, conversion = await self.db.funnel_report()
            views = conversion['views']
            buyers = conversion['buyers']
            percentage = f'{buyers / views * 100:.1f}%' if views else 'hali ma’lumot yo‘q'
            await call.message.answer('<b>📈 Sotuv bosqichlari</b>\n\n'
                f'Botni ochgan foydalanuvchilar: {stages.get("start", {}).get("unique", 0)}\n'
                f'Kursni ko‘rgan foydalanuvchi/kurs juftliklari: {views}\n'
                f'Xaridni boshlagan juftliklar: {stages.get("checkout", {}).get("unique", 0)}\n'
                f'Ko‘rganlar orasida to‘lagan juftliklar: {buyers}\n'
                f'Ko‘rish → xarid: {percentage}\n\n'
                'Takroriy bosishlar bitta foydalanuvchi/kurs sifatida hisoblanadi. '
                'Admin harakatlari kiritilmaydi. Hisob ushbu funksiya yoqilgandan beri yig‘iladi.\n\n'
                'Ko‘rish ko‘p, xarid kam bo‘lsa: kurs kimga mosligi, o‘quv natijalari va demo darsini aniqlashtiring.',
                reply_markup=keyboard([('📚 Kurslarni sozlash', 'a:list:0'), ('⚙️ Admin panel', 'a:menu')]))
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
            await call.message.answer('✅ Kurs saqlandi. Endi kimlar uchunligi, o‘quv natijalari va tanishuv darsini qo‘shib, kurs sahifasini kuchaytiring.',
                reply_markup=keyboard([('📚 Kurslar', 'a:list:0'), ('⚙️ Admin panel', 'a:menu')]))
        elif len(bits) >= 3:
            cid = int(bits[2])
            c = await self.db.one('courses', {'id': cid, 'deleted': 0})
            if not c:
                await call.message.answer('Kurs topilmadi.')
                return
            if action == 'course':
                await call.message.answer(f'#{cid} <b>{escape(c["name"])}</b>\n{escape(c["description"])}\n{money(c["price"])}\nKanal: <code>{c["channel_chat_id"]}</code>\nHolat: {"faol" if c["active"] else "nofaol"}\nhttps://t.me/{self.username}?start=course_{cid}',
                    reply_markup=keyboard([('✏️ Asosiy ma’lumotlar', f'a:edit:{cid}'), ('🎯 Sotuv sahifasini sozlash', f'a:marketing:{cid}'),
                        ('👀 Xaridor ko‘rinishi', f'a:preview:{cid}'), ('📣 Reklama uchun matn', f'a:post:{cid}'),
                        ('🔄 Faol/nofaol', f'a:toggle:{cid}'), ('🗑 O‘chirish', f'a:delete:{cid}'), ('📚 Kurslar', 'a:list:0')]))
            elif action == 'marketing':
                await state.clear()
                items = [(f'{"✅" if c.get(field) else "➕"} {title}', f'a:field:{cid}:{field}')
                         for field, (title, _) in MARKETING_FIELDS.items()]
                await call.message.answer('<b>🎯 Xaridorga qaror qilishda yordam bering</b>\n\n'
                    'Kurs kimga mosligini, o‘quvchi nimalarni o‘rganishini va darslar formatini aniq yozing. '
                    'Bepul tanishuv darsi ishonchni oshirishga yordam beradi.\n\n'
                    'Faqat kursda haqiqatdan mavjud ma’lumotlarni kiriting. Maydonni tanlang:',
                    reply_markup=keyboard(items + [('👀 Ko‘rib chiqish', f'a:preview:{cid}'), ('⬅️ Kursga qaytish', f'a:course:{cid}')]))
            elif action == 'field':
                field = bits[3] if len(bits) == 4 else ''
                if field not in MARKETING_FIELDS:
                    await call.message.answer('Maydon topilmadi.')
                    return
                await state.clear()
                await state.update_data(course_id=cid, field=field)
                await state.set_state(MarketingForm.value)
                title, maximum = MARKETING_FIELDS[field]
                await call.message.answer(f'<b>{title}</b>\n\nHozirgi qiymat:\n{escape(c.get(field) or "Kiritilmagan")}\n\n'
                    f'Yangi qiymatni yuboring (1–{maximum} belgi). Tozalash: <code>-</code>. Bekor qilish: /cancel.\n'
                    + ('Faqat ochiq demo dars HTTPS havolasini kiriting. Pullik kanal taklifini qo‘ymang.' if field == 'preview_url'
                       else 'Har bir fikrni yangi qatordan yozishingiz mumkin. Aniq va haqqoniy ma’lumot kiriting.'))
            elif action == 'preview':
                await call.message.answer(course_card(c), reply_markup=keyboard([
                    ('📖 Dastur', f'a:program:{cid}'), ('⬅️ Sozlashga qaytish', f'a:marketing:{cid}')]))
            elif action == 'program':
                await call.message.answer(course_details(c), reply_markup=keyboard([('⬅️ Ko‘rinishga qaytish', f'a:preview:{cid}')]))
            elif action == 'post':
                await call.message.answer('Quyidagi matnni reklama kanalingiz uchun nusxalashingiz mumkin. Kurs havolasi xaridorni shu kursga olib keladi.')
                await call.message.answer(course_card(c) + '\n\n👉 Kurs haqida va xarid:\n' + course_link(self.username, cid),
                    reply_markup=keyboard([('📤 Kurs havolasini ulashish', share_link(self.username, c)), ('⬅️ Kursga qaytish', f'a:course:{cid}')]))
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
            await message.answer('Tavsifni kiriting (1–2000 belgi).\n\nMaslahat: kurs qaysi muammoni yechishini va o‘quvchi nimani o‘rganishini aniq ayting. '
                                 'Asossiz daromad va’dalari bermang. Dastur va kimlar uchunligini keyin alohida qo‘shishingiz mumkin.')
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

    async def marketing_form(self, message, state: FSMContext):
        if message.from_user.id not in self.cfg.admins or message.chat.type != 'private':
            return
        data = await state.get_data()
        field, cid = data.get('field'), data.get('course_id')
        try:
            text = validate_marketing(field, (message.text or '').strip())
            await self.db.course_change(message.from_user.id, 'edit', {field: text}, cid=cid)
        except ValueError as exc:
            await message.answer(escape(str(exc)))
            return
        await state.clear()
        await message.answer('✅ Sotuv sahifasi yangilandi.', reply_markup=keyboard([
            ('🎯 Boshqa maydonlar', f'a:marketing:{cid}'), ('👀 Natijani ko‘rish', f'a:preview:{cid}')]))

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
        public_commands = [BotCommand(command='start', description='Bosh sahifa'),
            BotCommand(command='courses', description='Kurslarni tanlash'),
            BotCommand(command='purchases', description='Mening xaridlarim'),
            BotCommand(command='help', description='Savollar va yordam'), BotCommand(command='id', description='Telegram ID')]
        await bot.set_my_commands(public_commands)
        for admin in cfg.admins:
            with contextlib.suppress(TelegramAPIError):
                await bot.set_my_commands(public_commands + [BotCommand(command='admin', description='Admin panel'),
                    BotCommand(command='health', description='Ulanishni tekshirish')], scope=BotCommandScopeChat(chat_id=admin))
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
import re
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
    brand: str = 'Kurslar markazi'
    welcome: str = 'Maqsadingizga mos kursni tanlang. Dastur, narx va kirish tartibi bilan tanishib, qaror qiling.'
    support_username: str = ''

    @classmethod
    def load(cls):
        load_dotenv(ROOT / '.env')
        required = ['BOT_TOKEN', 'ADMIN_IDS', 'TEZCHECK_CASH_DESK_CODE', 'MONGODB_URI']
        if any(not os.getenv(k, '').strip() for k in required):
            raise ValueError('.env ichida BOT_TOKEN, ADMIN_IDS, MONGODB_URI va Tezcheck sozlamalarini to‘ldiring.')
        api_key = os.getenv('TEZCHECK_API_TOKEN', '').strip() or os.getenv('TEZCHECK_API_KEY', '').strip()
        if not api_key:
            raise ValueError('.env ichida TEZCHECK_API_TOKEN ni to‘ldiring (eski TEZCHECK_API_KEY ham ishlaydi).')
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
        base = (os.getenv('TEZCHECK_API_BASE_URL', '').strip() or os.getenv('TEZCHECK_BASE_URL', '').strip()
                or 'https://api.tezcheck.uz/api/merchant/v1').rstrip('/')
        if urlparse(base).scheme != 'https' or not urlparse(base).hostname:
            raise ValueError('TEZCHECK_API_BASE_URL HTTPS manzil bo‘lishi kerak.')
        brand = os.getenv('BRAND_NAME', '').strip() or 'Kurslar markazi'
        welcome = os.getenv('WELCOME_TEXT', '').strip() or cls.__dataclass_fields__['welcome'].default
        support = os.getenv('SUPPORT_USERNAME', '').strip().lstrip('@')
        if len(brand) > 80 or len(welcome) > 600:
            raise ValueError('BRAND_NAME 80, WELCOME_TEXT 600 belgidan oshmasin.')
        if support and not re.fullmatch(r'[A-Za-z][A-Za-z0-9_]{4,31}', support):
            raise ValueError('SUPPORT_USERNAME: Telegram username kiriting, masalan @kurs_yordam.')
        emoji = os.getenv('CUSTOM_EMOJI_ID', '').strip()
        if emoji and not emoji.isdigit():
            raise ValueError('CUSTOM_EMOJI_ID raqamlardan iborat yoki bo‘sh bo‘lsin.')
        interval = int(os.getenv('POLL_INTERVAL', '60'))
        hours = int(os.getenv('INVITE_TTL_HOURS', '24'))
        if interval < 30 or not 1 <= hours <= 168:
            raise ValueError('POLL_INTERVAL >= 30, INVITE_TTL_HOURS 1–168 bo‘lsin.')
        return cls(os.environ['BOT_TOKEN'].strip(), admins, api_key,
                   os.environ['TEZCHECK_CASH_DESK_CODE'].strip(), base,
                   database, mongo_uri, interval, hours, emoji, brand, welcome, support)

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
            await self.db.funnel.create_index([('event', 1), ('user_id', 1), ('course_id', 1)], unique=True)
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

    async def track(self, event, user_id, course_id=0):
        query = {'event': event, 'user_id': user_id, 'course_id': course_id}
        await self.db.funnel.update_one(query, {'$setOnInsert': {'first_at': now()},
            '$set': {'last_at': now()}, '$inc': {'hits': 1}}, upsert=True)

    async def funnel_report(self):
        stages = [row async for row in await self.db.funnel.aggregate([
            {'$group': {'_id': '$event', 'unique': {'$sum': 1}, 'hits': {'$sum': '$hits'}}}])]
        conversion = [row async for row in await self.db.funnel.aggregate([
            {'$match': {'event': 'course_view'}},
            {'$lookup': {'from': 'orders', 'let': {'user': '$user_id', 'course': '$course_id'},
                'pipeline': [{'$match': {'$expr': {'$and': [
                    {'$eq': ['$user_id', '$$user']}, {'$eq': ['$course_id', '$$course']},
                    {'$eq': ['$status', 'paid']}]}}}], 'as': 'paid_orders'}},
            {'$group': {'_id': None, 'views': {'$sum': 1}, 'buyers': {'$sum': {
                '$cond': [{'$gt': [{'$size': '$paid_orders'}, 0]}, 1, 0]}}}}
        ])]
        return {row['_id']: row for row in stages}, conversion[0] if conversion else {'views': 0, 'buyers': 0}

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

## marketing.py
````text
from html import escape
from urllib.parse import urlencode, urlparse


MARKETING_FIELDS = {
    'audience': ('Kimlar uchun', 350),
    'outcomes': ('Nimalarni o‘rganasiz', 700),
    'program': ('Kurs dasturi', 1500),
    'instructor': ('Ustoz haqida', 180),
    'duration': ('Davomiylik va format', 180),
    'preview_url': ('Bepul tanishuv havolasi', 500),
}


def money(value):
    return f'{value:,}'.replace(',', ' ') + ' so‘m'


def course_link(username, cid):
    return f'https://t.me/{username}?start=course_{cid}'


def share_link(username, course):
    return 'https://t.me/share/url?' + urlencode({
        'url': course_link(username, course['id']),
        'text': course['name'] + ' — ' + money(course['price'])})


def validate_marketing(field, text):
    if field not in MARKETING_FIELDS:
        raise ValueError('Maydon topilmadi.')
    if text == '-':
        return ''
    title, maximum = MARKETING_FIELDS[field]
    if not 1 <= len(text) <= maximum:
        raise ValueError(f'{title}: 1–{maximum} belgi kiriting yoki tozalash uchun - yuboring.')
    if field == 'preview_url':
        parsed = urlparse(text)
        if (parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password
                or any(c.isspace() for c in text)):
            raise ValueError('Tanishuv uchun ochiq HTTPS havola kiriting. Yopiq kurs taklif havolasini yozmang.')
        if parsed.hostname.lower() in ('t.me', 'www.t.me', 'telegram.me') and (
                parsed.path.startswith('/+') or parsed.path.startswith('/joinchat/')):
            raise ValueError('Yopiq kanal taklifini tanishuv sifatida qo‘shmang. Ochiq demo dars havolasini kiriting.')
    return text


def course_card(course):
    description = course['description']
    if len(description) > 1200:
        description = description[:1199] + '…'
    parts = [f'📚 <b>{escape(course["name"])}</b>', escape(description)]
    for field in ('audience', 'outcomes', 'duration', 'instructor'):
        if course.get(field):
            parts.append(f'<b>{MARKETING_FIELDS[field][0]}</b>\n{escape(course[field])}')
    parts.extend([f'💳 <b>Narxi: {money(course["price"])}</b>',
        '🔐 To‘lov tasdiqlangach, shu kursning yopiq kanaliga kirish havolasini olasiz.',
        'Boshlashga tayyormisiz? Quyidagi tugma orqali davom eting.'])
    return '\n\n'.join(parts)


def course_details(course):
    parts = [f'📖 <b>{escape(course["name"])}</b>', escape(course['description'])]
    if course.get('program'):
        parts.append('<b>Kurs dasturi</b>\n' + escape(course['program']))
    parts.append('Savolingiz qolgan bo‘lsa, to‘lovdan oldin yordam bo‘limiga yozing.')
    return '\n\n'.join(parts)


ORDER_LABELS = {'creating': 'Hisob tayyorlanmoqda', 'pending': 'To‘lov kutilmoqda',
                'paid': 'To‘lov tasdiqlangan', 'review': 'Admin tekshiruvi kerak'}

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
TEZCHECK_API_TOKEN=
TEZCHECK_CASH_DESK_CODE=
TEZCHECK_API_BASE_URL=https://api.tezcheck.uz/api/merchant/v1
MONGODB_URI=
MONGODB_DATABASE=kurs_sotish_bot
POLL_INTERVAL=60
INVITE_TTL_HOURS=24
CUSTOM_EMOJI_ID=
BRAND_NAME=Kurslar markazi
WELCOME_TEXT=Maqsadingizga mos kursni tanlang. Dastur, narx va kirish tartibi bilan tanishib, qaror qiling.
SUPPORT_USERNAME=

````

## .gitignore
````text
.env
.venv/
.test-runtime/
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

    async def test_funnel_deduplicates_views_and_matches_paid_order(self):
        await self.db.track('course_view', 42, self.cid)
        await self.db.track('course_view', 42, self.cid)
        await self.db.track('checkout', 42, self.cid)
        order = await self.db.order(42, self.cid)
        await self.db.mark_paid(order, 'pay_funnel')
        stages, conversion = await self.db.funnel_report()
        self.assertEqual(stages['course_view']['unique'], 1)
        self.assertEqual(stages['course_view']['hits'], 2)
        self.assertEqual(conversion['views'], 1)
        self.assertEqual(conversion['buyers'], 1)


if __name__ == '__main__':
    unittest.main()

````

## tests/test_config.py
````text
import os
import unittest
from unittest.mock import patch

from config import Config


class ConfigTests(unittest.TestCase):
    def load_config(self, values):
        base = {'BOT_TOKEN': 'test-token', 'ADMIN_IDS': '1,2',
                'TEZCHECK_CASH_DESK_CODE': 'desk', 'MONGODB_URI': 'mongodb://localhost:27017'}
        with patch.dict(os.environ, {**base, **values}, clear=True), patch('config.load_dotenv'):
            return Config.load()

    def test_new_names(self):
        cfg = self.load_config({'TEZCHECK_API_TOKEN': 'new-token',
                               'TEZCHECK_API_BASE_URL': 'https://example.test/api/'})
        self.assertEqual(cfg.api_key, 'new-token')
        self.assertEqual(cfg.base_url, 'https://example.test/api')

    def test_legacy_names_still_work(self):
        cfg = self.load_config({'TEZCHECK_API_KEY': 'old-token',
                               'TEZCHECK_BASE_URL': 'https://old.example.test/api'})
        self.assertEqual(cfg.api_key, 'old-token')
        self.assertEqual(cfg.base_url, 'https://old.example.test/api')

    def test_new_names_take_priority(self):
        cfg = self.load_config({'TEZCHECK_API_TOKEN': 'new', 'TEZCHECK_API_KEY': 'old',
            'TEZCHECK_API_BASE_URL': 'https://new.example.test', 'TEZCHECK_BASE_URL': 'https://old.example.test'})
        self.assertEqual(cfg.api_key, 'new')
        self.assertEqual(cfg.base_url, 'https://new.example.test')

    def test_blank_new_values_fall_back_to_old(self):
        cfg = self.load_config({'TEZCHECK_API_TOKEN': ' ', 'TEZCHECK_API_KEY': 'old',
            'TEZCHECK_API_BASE_URL': '', 'TEZCHECK_BASE_URL': 'https://old.example.test'})
        self.assertEqual(cfg.api_key, 'old')
        self.assertEqual(cfg.base_url, 'https://old.example.test')

    def test_missing_api_token(self):
        with self.assertRaisesRegex(ValueError, 'TEZCHECK_API_TOKEN'):
            self.load_config({})

    def test_brand_and_support(self):
        cfg = self.load_config({'TEZCHECK_API_TOKEN': 'key', 'BRAND_NAME': 'Test maktab', 'SUPPORT_USERNAME': '@test_support'})
        self.assertEqual(cfg.brand, 'Test maktab')
        self.assertEqual(cfg.support_username, 'test_support')

    def test_invalid_support_url_is_rejected(self):
        with self.assertRaisesRegex(ValueError, 'SUPPORT_USERNAME'):
            self.load_config({'TEZCHECK_API_TOKEN': 'key', 'SUPPORT_USERNAME': 'https://example.test'})


if __name__ == '__main__':
    unittest.main()

````

## tests/test_marketing.py
````text
import unittest
from html.parser import HTMLParser
from types import SimpleNamespace
from unittest.mock import AsyncMock
from urllib.parse import parse_qs, urlparse

from main import Shop
from marketing import MARKETING_FIELDS, course_card, course_details, share_link, validate_marketing


class PlainText(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts = []

    def handle_data(self, data):
        self.parts.append(data)


class MarketingTests(unittest.TestCase):
    def setUp(self):
        self.course = {'id': 1, 'name': 'Python & <Django>', 'price': 150000,
                       'description': 'Amaliy <darslar>'}

    def test_existing_course_without_marketing_fields(self):
        card = course_card(self.course)
        self.assertIn('150 000', card)
        self.assertIn('Python &amp; &lt;Django&gt;', card)
        self.assertNotIn('Kimlar uchun', card)

    def test_marketing_text_is_escaped(self):
        self.course['outcomes'] = '<a href="https://example.test">Havola</a>'
        self.assertIn('&lt;a', course_card(self.course))
        self.assertNotIn('<a href=', course_card(self.course))

    def test_longest_valid_course_fits_telegram_limit(self):
        self.course.update(name='x' * 120, description='x' * 2000)
        for field, (_, size) in MARKETING_FIELDS.items():
            if field != 'preview_url':
                self.course[field] = 'x' * size
        for text in (course_card(self.course), course_details(self.course)):
            parser = PlainText()
            parser.feed(text)
            self.assertLessEqual(len(''.join(parser.parts)), 4096)

    def test_private_invites_and_unsafe_preview_are_rejected(self):
        for url in ('http://example.test', 'javascript:alert(1)', 'https://t.me/+secret',
                    'https://t.me/joinchat/secret', 'https://user:pass@example.test'):
            with self.subTest(url=url), self.assertRaises(ValueError):
                validate_marketing('preview_url', url)
        self.assertEqual(validate_marketing('preview_url', 'https://example.test/demo'), 'https://example.test/demo')

    def test_clear_marketing_field(self):
        self.assertEqual(validate_marketing('outcomes', '-'), '')

    def test_share_url_is_encoded(self):
        query = parse_qs(urlparse(share_link('test_bot', self.course)).query)
        self.assertEqual(query['url'], ['https://t.me/test_bot?start=course_1'])
        self.assertIn('Python & <Django>', query['text'][0])


class CustomerFlowTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        config = SimpleNamespace(admins=frozenset({1}), support_username='', emoji='',
                                 brand='Maktab', welcome='Kursni tanlang.')
        self.db = SimpleNamespace(one=AsyncMock(return_value=None), rows=AsyncMock(return_value=[]),
                                  order=AsyncMock(), track=AsyncMock(), course_change=AsyncMock())
        self.api = SimpleNamespace(create=AsyncMock())
        self.shop = Shop(config, self.db, self.api, SimpleNamespace())
        self.shop.username = 'test_bot'
        self.message = SimpleNamespace(chat=SimpleNamespace(type='private'), answer=AsyncMock())

    def call(self, data, uid=42):
        return SimpleNamespace(data=data, from_user=SimpleNamespace(id=uid),
                               message=self.message, answer=AsyncMock())

    async def test_order_lookup_is_scoped_to_customer(self):
        await self.shop.customer_callback(self.call('u:order:99'))
        self.db.one.assert_awaited_once_with('orders', {'id': 99, 'user_id': 42})
        self.api.create.assert_not_awaited()

    async def test_paid_course_never_creates_second_bill(self):
        self.db.order.return_value = {'id': 7, 'course_id': 1, 'user_id': 42, 'status': 'paid'}
        self.shop.deliver = AsyncMock()
        await self.shop.customer_callback(self.call('u:buy:1'))
        self.shop.deliver.assert_awaited_once()
        self.api.create.assert_not_awaited()

    async def test_admin_buttons_hidden_from_customer(self):
        await self.shop.catalog(self.message, 42)
        markup = self.message.answer.call_args.kwargs['reply_markup']
        self.assertFalse(any((b.callback_data or '').startswith('a:') for row in markup.inline_keyboard for b in row))

    async def test_non_admin_cannot_edit_marketing(self):
        state = SimpleNamespace(get_data=AsyncMock(), clear=AsyncMock())
        message = SimpleNamespace(chat=SimpleNamespace(type='private'), from_user=SimpleNamespace(id=42))
        await self.shop.marketing_form(message, state)
        self.db.course_change.assert_not_awaited()


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
  marketing.py              kurs taqdimoti, reklama havolalari va matnlar
  requirements.txt
  .env.example
  .gitignore
  README.md
  tests/test_payments.py
  tests/test_config.py
  tests/test_marketing.py
  MARKETING_GUIDE.md        kurs sotuvini sozlash bo‘yicha amaliy yo‘riqnoma
  FULL_CODE.md             barcha fayllar kodining birlashtirilgan nusxasi
```

## Windows (PowerShell)

Python 3.11 yoki yangiroq versiyani python.org saytidan o‘rnating va PATH belgisini yoqing.

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
if (-not (Test-Path .env)) { Copy-Item .env.example .env }
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
[ -f .env ] || cp .env.example .env
nano .env
python main.py
```

## Sozlash

- `BOT_TOKEN`: Telegramdagi rasmiy @BotFather orqali `/newbot` bilan yarating.
- `ADMIN_IDS`: adminlarning raqamli Telegram IDlari, vergul bilan. Botdagi `/id` o‘z IDingizni ko‘rsatadi. Username yozmang.
- `TEZCHECK_API_TOKEN`: Tezcheck savdogar kabinetida API kalit yarating.
- `TEZCHECK_CASH_DESK_CODE`: kabinetdagi kassa kodi; bu kassa IDsi emas.
- `TEZCHECK_API_BASE_URL`: namunadagi rasmiy HTTPS manzil.
- `MONGODB_URI`: Atlas kabinetidagi Connect → Drivers bo‘limidan olingan maxfiy ulanish manzili.
- `MONGODB_DATABASE`: baza nomi, odatda `kurs_sotish_bot`.
- `POLL_INTERVAL`: tekshirishlar orasidagi soniyalar, kamida 30.
- `INVITE_TTL_HOURS`: kirish havolasi muddati, 1–168 soat.
- `CUSTOM_EMOJI_ID`: ixtiyoriy haqiqiy Telegram custom emoji IDsi. Bo‘sh bo‘lsa oddiy emoji ishlaydi. Telegram hisob/bot cheklovlariga qarab custom emoji oddiy emoji sifatida ko‘rinishi mumkin.
- `BRAND_NAME`: bosh sahifada ko‘rinadigan maktab/brend nomi, 80 belgigacha.
- `WELCOME_TEXT`: salomlashuvdan keyingi tanishtiruv, 600 belgigacha.
- `SUPPORT_USERNAME`: yordam beradigan haqiqiy Telegram username, masalan `@kurs_yordam`. Bo‘sh bo‘lsa shaxsiy yozish tugmasi ko‘rsatilmaydi; yordam bo‘limi ishlaydi.

Eski `TEZCHECK_API_KEY` va `TEZCHECK_BASE_URL` nomlari ham ishlaydi. Yangi va eski nom bir vaqtda to‘ldirilsa, `TEZCHECK_API_TOKEN` va `TEZCHECK_API_BASE_URL` ustun turadi. Yangi nom bo‘sh bo‘lsa, eski qiymat ishlatiladi. `.env`dagi boshqa kalitlarni o‘chirish yoki qayta yozish shart emas.

```dotenv
TEZCHECK_API_BASE_URL=https://api.tezcheck.uz/api/merchant/v1
TEZCHECK_API_TOKEN=
TEZCHECK_CASH_DESK_CODE=
```

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

## Sotuvga mos xaridor oqimi

- Bosh sahifa brend va tanishtiruv matni bilan ochiladi. Kurslar narxi bilan sahifalanadi.
- Kurs sahifasida auditoriya, o‘quv natijalari, davomiylik/format, ustoz va aniq xarid tugmasi ko‘rinadi.
- Dastur va to‘liq tavsif alohida ochiladi. Demo havolasi kiritilsa bepul tanishuv tugmasi chiqadi.
- `/purchases` yoki **Mening xaridlarim** oldingi buyurtmalarni ko‘rsatadi. Xarid qilingan kurs uchun yangi hisob yaratilmaydi.
- `/help` xarid tartibi va to‘lov savollariga javob beradi; kontakt sozlansa maslahatchiga yozish tugmasi ko‘rinadi.
- Kursni ulashish tugmasi [Telegramning rasmiy share havolasi](https://core.telegram.org/widgets/share) orqali ishlaydi. Foydalanuvchi yuborishni o‘zi tanlaydi.

Admin: **Kurslar → kurs → Sotuv sahifasini sozlash**. Maydonlar alohida tahrirlanadi, `-` bilan tozalanadi va auditga yoziladi. Marketing maydonlari ixtiyoriy: oldingi kurslar o‘zgarishsiz ishlaydi. **Reklama uchun matn** mavjud kurs ma’lumotlari va deep linkdan post tayyorlaydi; avtomatik reklama yuborilmaydi. [Amaliy marketing yo‘riqnomasi](MARKETING_GUIDE.md).

**Sotuv bosqichlari** bo‘limi kursni ko‘rish → xaridni boshlash → tasdiqlangan xaridni ko‘rsatadi. `funnel` kolleksiyasi har bir event/user/kurs uchun bitta yozuv va takroriy ko‘rish hisobini saqlaydi. Admin ko‘rishlari chiqarib tashlanadi. Konversiya kuzatilgan user/kurs juftliklaridan to‘laganlar ulushi; statistika yangi funksiya yoqilgan vaqtdan boshlanadi.

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

Konfiguratsiya testlari yangi/eski Tezcheck nomlari va ustuvorligini tekshiradi. Marketing testlari matn uzunligi, HTML escaping, demo URL, ulashish havolasi, xaridorning o‘z orderiga kirishi va to‘langan kurs uchun ikkinchi hisob ochilmasligini tekshiradi. Bu unit testlar haqiqiy `.env`ni o‘qimaydi.

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
10. Kursning sotuv maydonlari va demo darsini to‘ldiring. Admin preview va boshqa xaridorda kartani ochib tekshiring; xaridorning `/purchases` va `/help` tugmalarini sinang. **Sotuv bosqichlari**da admin ko‘rishlari qo‘shilmaganini tekshiring.

Ushbu tayyorlash muhitida `.venv` ko‘rsatgan Python bajariluvchi fayli topilmadi; avtomatik testlar va real to‘lov yakunigacha ishga tushirish bajarilmagan. Ishga tushirish uchun ishlaydigan Python, haqiqiy tokenlar, kassa va kanal huquqlari kerak.

````

## MARKETING_GUIDE.md
````text
# Kurs sotuvini tayyorlash

## Botni brendingizga moslang

`.env`dagi `BRAND_NAME`, `WELCOME_TEXT`, `SUPPORT_USERNAME` qiymatlarini o‘zingizga moslang va botni qayta ishga tushiring. Savolga javob beradigan haqiqiy yordam akkauntini belgilang.

## Kursning taklifini aniq yozing

`/admin` → Kurslar → kurs → **Sotuv sahifasini sozlash**.

- **Kimlar uchun:** o‘quvchining boshlang‘ich darajasi va kursga kerakli bilimlar.
- **Nimalarni o‘rganasiz:** 3–5 ta aniq ko‘nikma. «Zo‘r mutaxassis bo‘lasiz» o‘rniga o‘quvchi dars oxirida bajara oladigan amallarni yozing.
- **Kurs dasturi:** modullarni ketma-ket bering; kursda yo‘q mavzularni qo‘shmang.
- **Ustoz haqida:** ism, tegishli tajriba va tekshirish mumkin bo‘lgan ma’lumotlar.
- **Davomiylik va format:** dars davomiyligi, yozib olingan/jonli ekanligi, materiallarga kirish muddati. Mentor yordami bo‘lsa uning shartlarini aniq ayting.
- **Bepul tanishuv havolasi:** ochiq, haqiqiy demo dars. Pullik kanalning taklif havolasini qo‘ymang.

Har bir maydon ixtiyoriy. `-` yuborilsa qiymat tozalanadi. Oldin yaratilgan kurslar ham ishlashda davom etadi. **Xaridor ko‘rinishi** bilan natijani tekshiring.

## Reklama postini tayyorlang

Kurs kartasida **Reklama uchun matn** tugmasini bosing. Bot kursning saqlangan ma’lumotlari va aynan shu kursga olib boradigan havolani beradi. Matnni kanalingizga nusxalang; ulashish tugmasi orqali kurs nomi, narxi va havolasini Telegramda ulashish mumkin. Bot sizning nomingizdan reklama yubormaydi.

Postning birinchi jumlasida auditoriyaning muammosini ayting. Keyin kurs qaysi ko‘nikmalarni o‘rgatishini ko‘rsating. Narx va xarid tugmasini ko‘rinadigan qoldiring. Soxta chegirma, uydirma o‘quvchi fikri yoki asossiz daromad kafolatini qo‘shmang. Haqiqiy chegirma bo‘lmasa «faqat bugun» deb yozmang.

## Xaridor yo‘lini tekshiring

Reklama havolasi → kurs sahifasi → dastur/demo → xarid → Tezcheck → tasdiqlangan to‘lov → kanalga kirish.

Xaridor `/purchases` orqali oldingi to‘lovi yoki kursini ochadi. To‘lov boshlangan bo‘lsa mavjud hisobni davom ettiradi; to‘langan bo‘lsa yana hisob ochilmaydi. `/help` savollarga javob va yordam kontaktini ko‘rsatadi.

## Natijani o‘lchang

`/admin` → **Sotuv bosqichlari**. Ko‘rish va xaridni boshlash bitta foydalanuvchi/kurs uchun bir marta hisoblanadi. «Botni ochganlar» foydalanuvchilar soni, qolgan kurs bosqichlari esa foydalanuvchi/kurs juftliklaridir. Admin harakatlari hisobga olinmaydi. Konversiya kursni ko‘rgan juftliklar ichida ayni kurs uchun haqiqiy to‘lov qilganlar ulushidir. Kuzatuv funksiya yoqilgan vaqtdan boshlanadi; kampaniya manbasi/UTM bo‘yicha ajratish hozircha yo‘q.

- Bot ochiladi, kurs ko‘rilmaydi: reklama auditoriyasi va kurs nomini tekshiring.
- Kurs ko‘riladi, xarid boshlanmaydi: dastur, o‘quv natijalari, mos auditoriya va demo mazmunini tekshiring.
- Xarid boshlanadi, to‘lov tugamaydi: `/health`, to‘lov usullari va yordam kontaktini tekshiring.

Bir vaqtda bir asosiy o‘zgarish qiling va yetarli kuzatuv yig‘ilgach baholang. Bot avtomatik reklama yoki xaridni tugatishga undovchi ommaviy xabarlar yubormaydi.

````
