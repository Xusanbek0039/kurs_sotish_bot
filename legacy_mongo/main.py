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
