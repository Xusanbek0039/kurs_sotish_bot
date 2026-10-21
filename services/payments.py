"""To‘lov oqimi: buyurtma → Tezcheck hisobi → tasdiq → xarid → kanal havolasi.

Muhim qoidalar:
* Buyurtma faqat Tezcheck API javobi (POST /bills/{bill}) yoki imzosi tekshirilgan webhook bilan “to‘landi” bo‘ladi.
* To‘lov tasdig‘i va xarid yaratish bitta tranzaksiyada (`Database.mark_paid`).
* Havola yaratishdan oldin xarid atomar band qilinadi (`claim_purchase`), shuning uchun ikki marta yuborilmaydi.
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
import weakref
from dataclasses import dataclass
from datetime import timedelta

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError, TelegramForbiddenError
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from database import Database, PaidResult, parse_ts, utcnow
from locales import t
from services.tezcheck import TezcheckClient, TezcheckError, Verdict, evaluate_bill
from services.ui import TASHKENT, course_name, emoji, h

log = logging.getLogger(__name__)

MAX_USER_LINK_REQUESTS = 3     # foydalanuvchi o‘zi so‘ray oladigan havolalar soni
MAX_DELIVERY_ATTEMPTS = 20
MEMBER_STATUSES = {'creator', 'administrator', 'member'}


@dataclass
class Checkout:
    kind: str                   # ready | purchased | unavailable | error
    order: dict | None = None
    purchase: dict | None = None
    error: str | None = None


class PaymentService:
    def __init__(self, db: Database, api: TezcheckClient, bot: Bot, settings):
        self.db, self.api, self.bot, self.settings = db, api, bot, settings
        self._locks: weakref.WeakValueDictionary[int, asyncio.Lock] = weakref.WeakValueDictionary()
        self._tasks: set[asyncio.Task] = set()

    def _lock(self, order_id: int) -> asyncio.Lock:
        lock = self._locks.get(order_id)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[order_id] = lock
        return lock

    async def _lang(self, user_id: int) -> str:
        user = await self.db.get_user(user_id)
        return (user or {}).get('language') or self.settings.default_language

    def spawn(self, coro) -> None:
        """Fon vazifasi (masalan, webhookdan keyin havola yuborish); o‘chishda kutib olinadi."""
        task = asyncio.create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def shutdown(self) -> None:
        if self._tasks:
            await asyncio.wait(self._tasks, timeout=10)

    # ------------------------------------------------------------------ checkout
    async def checkout(self, user_id: int, course_id: int) -> Checkout:
        kind, row = await self.db.get_or_create_open_order(user_id, course_id)
        if kind == 'purchased':
            return Checkout('purchased', purchase=row)
        if kind == 'unavailable':
            return Checkout('unavailable')
        async with self._lock(row['id']):
            order = await self.db.get_order(row['id'])
            if order['status'] == 'pending' and order['payment_url']:
                return Checkout('ready', order)
            if order['status'] != 'new':
                return Checkout('error', order, error=order['status'])
            amount_minor = order['amount_uzs'] * 100
            title = f'{order["course_title"]} — #{order["id"]}'[:191]
            try:
                bill = await self.api.create_bill(
                    amount_minor=amount_minor, title=title.ljust(2, '.'),
                    external_reference=order['external_reference'],
                    idempotency_key=f'{order["idempotency_key"]}-{amount_minor}')
            except TezcheckError as exc:
                await self.db.note_order_check(order['id'], exc.code)
                log.warning('Hisob yaratilmadi: buyurtma=%s %s', order['id'], exc)
                return Checkout('error', order, error=exc.code)
            await self.db.set_order_bill(order['id'], bill.bill_id, bill.payment_url)
            return Checkout('ready', await self.db.get_order(order['id']))

    # ------------------------------------------------------------- holat tekshirish
    async def check_order(self, order_id: int, notify: bool = False) -> str:
        """Natija: paid | pending | expired | test | review | no_bill | error | not_found."""
        async with self._lock(order_id):
            order = await self.db.get_order(order_id)
            if not order:
                return 'not_found'
            if order['status'] == 'paid':
                return 'paid'
            if not order['tezcheck_bill_id']:
                return 'no_bill'
            try:
                bill = await self.api.get_bill(order['tezcheck_bill_id'])
            except TezcheckError as exc:
                await self.db.note_order_check(order_id, exc.code)
                return 'error'
            verdict = evaluate_bill(order, bill)
            if verdict is Verdict.PAID:
                await self.db.note_order_check(order_id)
                result = await self.db.mark_paid(order_id, bill.payment_id, 'live')
                await self._after_paid(order, result)
                return 'paid'
            if verdict is Verdict.PENDING:
                await self.db.note_order_check(order_id)
                return 'pending'
            if verdict is Verdict.EXPIRED:
                await self.db.note_order_check(order_id)
                if await self.db.set_order_status(order_id, 'expired', ('pending',)) and notify:
                    await self._notify_user(order['user_id'], 'payment_expired_notice', order_id=order_id)
                return 'expired'
            if verdict is Verdict.TEST:
                if order['last_error'] != 'test_payment':
                    await self.alert_admins('alert_test_payment', order_id=order_id, user_id=order['user_id'])
                await self.db.note_order_check(order_id, 'test_payment')
                return 'test'
            await self.db.set_order_status(order_id, 'review', ('pending',), 'mismatch')
            await self.alert_admins('alert_mismatch', order_id=order_id, user_id=order['user_id'])
            return 'review'

    async def _after_paid(self, order: dict, result: PaidResult) -> None:
        if result.duplicate and result.newly_paid:
            await self.alert_admins('alert_duplicate_payment', order_id=order['id'], user_id=order['user_id'])
        if result.purchase_id:
            await self.deliver(result.purchase_id)

    # --------------------------------------------------------------------- webhook
    async def process_webhook_event(self, event: dict) -> tuple[str, str | None]:
        """Imzosi tekshirilgan hodisani qayta ishlaydi. Natija: (processed|ignored, izoh)."""
        event_type = event.get('type')
        data = event.get('data')
        if not isinstance(data, dict) or not isinstance(event_type, str):
            return 'ignored', 'malformed'
        reference = data.get('external_reference')
        order = await self.db.get_order_by_reference(reference) if isinstance(reference, str) else None
        if not order:
            return 'ignored', 'unknown_order'

        if event_type == 'payment.succeeded':
            if data.get('environment') != 'live':
                # Hujjat: yuqoridagi `mode` emas, `data.environment` ishonchli. Test to‘lovi kurs bermaydi.
                return 'ignored', 'test_payment'
            payment_id = data.get('payment_id')
            bill_id = data.get('bill_id')
            if (data.get('state') != 'succeeded' or not isinstance(payment_id, str) or not payment_id
                    or type(data.get('amount_minor')) is not int or data['amount_minor'] != order['amount_uzs'] * 100
                    or data.get('currency') != 'UZS' or not isinstance(bill_id, str)
                    or (order['tezcheck_bill_id'] and bill_id != order['tezcheck_bill_id'])):
                if order['status'] != 'paid':
                    await self.db.set_order_status(order['id'], 'review', ('new', 'pending', 'expired'), 'mismatch')
                    await self.alert_admins('alert_mismatch', order_id=order['id'], user_id=order['user_id'])
                return 'ignored', 'mismatch'
            result = await self.db.mark_paid(order['id'], payment_id, 'live', bill_id)
            # Webhookga tez javob berish uchun havola yuborish fonda bajariladi.
            self.spawn(self._after_paid(order, result))
            return 'processed', None

        if event_type in ('payment.refunded', 'payment.partially_refunded', 'payment.charged_back',
                          'payment.disputed') and order['status'] == 'paid' and data.get('environment') == 'live':
            await self.alert_admins('alert_refund', order_id=order['id'], user_id=order['user_id'],
                                    event=h(event_type))
            return 'processed', None
        return 'ignored', event_type[:40]

    # ------------------------------------------------------------- havola yuborish
    async def deliver(self, purchase_id: int, actor: str = 'system', new_link: bool = False) -> str:
        """Kanalga shaxsiy havola yaratadi va yuboradi.

        actor: system (avtomatik), user (xaridor o‘zi so‘radi), admin (qayta yuborish).
        Natija: sent | busy | not_paid | member | limit | invite_failed | send_failed.
        """
        statuses = ('pending_invite', 'link_created', 'send_failed')
        if actor in ('user', 'admin'):
            statuses += ('invite_failed', 'sent')
        purchase = await self.db.claim_purchase(purchase_id, statuses)
        if not purchase:
            return 'busy'
        restore = 'sent' if purchase['invite_sent_at'] else ('link_created' if purchase['invite_link'] else 'pending_invite')
        user_id = purchase['user_id']
        try:
            if purchase['order_status'] != 'paid':
                # Himoya: to‘lov tasdiqlanmagan bo‘lsa, havola hech qachon yaratilmaydi.
                await self.db.release_purchase(purchase_id, restore)
                log.error('Xarid %s: buyurtma to‘lanmagan, havola berilmadi', purchase_id)
                return 'not_paid'
            lang = await self._lang(user_id)
            expires = parse_ts(purchase['invite_expires_at'])
            need_new = new_link or not purchase['invite_link'] or not expires or expires <= utcnow() + timedelta(minutes=5)

            if actor == 'user' and purchase['invite_sent_at']:
                with contextlib.suppress(TelegramAPIError):
                    member = await self.bot.get_chat_member(purchase['channel_chat_id'], user_id)
                    if member.status in MEMBER_STATUSES or getattr(member, 'is_member', False):
                        await self.db.release_purchase(purchase_id, restore)
                        return 'member'
                if need_new and purchase['invite_count'] >= MAX_USER_LINK_REQUESTS:
                    await self.db.release_purchase(purchase_id, restore)
                    return 'limit'

            if need_new:
                expire_at = utcnow() + timedelta(hours=self.settings.invite_link_ttl_hours)
                try:
                    invite = await self.bot.create_chat_invite_link(
                        purchase['channel_chat_id'], name=f'order-{purchase["order_id"]}'[:32],
                        expire_date=expire_at, member_limit=1)
                except TelegramAPIError as exc:
                    await self.db.mark_purchase_failed(purchase_id, 'invite_failed', type(exc).__name__ + ': ' + str(exc), None)
                    log.error('Kanal havolasi yaratilmadi: xarid=%s kurs=%s xato=%s', purchase_id,
                              purchase['course_id'], type(exc).__name__)
                    await self._notify_user(user_id, 'invite_failed_user', order_id=purchase['order_id'])
                    await self.alert_admins('alert_invite_failed', order_id=purchase['order_id'], user_id=user_id,
                                            course_id=purchase['course_id'])
                    return 'invite_failed'
                if purchase['invite_link']:
                    with contextlib.suppress(TelegramAPIError):
                        await self.bot.revoke_chat_invite_link(purchase['channel_chat_id'], purchase['invite_link'])
                await self.db.save_invite(purchase_id, invite.invite_link, expire_at)
                purchase['invite_link'], expires = invite.invite_link, expire_at
                restore = 'link_created'

            text = t(lang, 'access_granted', emoji=emoji(self.settings, 'success', '🎉'),
                     course=h(course_name(purchase, lang)), order_id=purchase['order_id'],
                     expires=expires.astimezone(TASHKENT).strftime('%d.%m.%Y %H:%M'))
            markup = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text=t(lang, 'btn_join_channel'), url=purchase['invite_link'], style='success')]])
            try:
                await self.bot.send_message(user_id, text, reply_markup=markup)
            except TelegramAPIError as exc:
                attempts = purchase['attempts']
                retry = None if attempts >= MAX_DELIVERY_ATTEMPTS else utcnow() + timedelta(
                    minutes=min(5 * 2 ** (attempts - 1), 360))
                await self.db.mark_purchase_failed(purchase_id, 'send_failed', type(exc).__name__, retry)
                log.warning('Foydalanuvchiga havola yuborilmadi: xarid=%s xato=%s (keyin qayta urinish)',
                            purchase_id, type(exc).__name__)
                return 'send_failed'
            await self.db.mark_purchase_sent(purchase_id)
            if self.settings.sticker_success:
                with contextlib.suppress(TelegramAPIError):
                    await self.bot.send_sticker(user_id, self.settings.sticker_success)
            return 'sent'
        except BaseException:
            with contextlib.suppress(Exception):
                await self.db.release_purchase(purchase_id, restore)
            raise

    async def deliver_pending_for_user(self, user_id: int) -> None:
        for purchase in await self.db.user_purchases(user_id):
            if purchase['status'] in ('pending_invite', 'link_created', 'send_failed'):
                await self.deliver(purchase['id'])

    # ---------------------------------------------------------------- xabarlar
    async def _notify_user(self, user_id: int, key: str, **kwargs) -> None:
        lang = await self._lang(user_id)
        try:
            await self.bot.send_message(user_id, t(lang, key, support=self.support_hint(lang), **kwargs))
        except TelegramForbiddenError:
            log.info('Foydalanuvchi %s botni bloklagan', user_id)
        except TelegramAPIError as exc:
            log.warning('Xabar yuborilmadi: %s', type(exc).__name__)

    def support_hint(self, lang: str) -> str:
        if self.settings.support_username:
            return t(lang, 'support_contact', username=self.settings.support_username)
        return t(lang, 'support_contact_admin')

    async def alert_admins(self, key: str, **kwargs) -> None:
        log.warning('Admin ogohlantirishi: %s %s', key, {k: v for k, v in kwargs.items() if k != 'event'})
        for admin_id in self.settings.admin_ids:
            lang = await self._lang(admin_id)
            with contextlib.suppress(TelegramAPIError):
                await self.bot.send_message(admin_id, t(lang, key, **kwargs))

    # ---------------------------------------------------------------- fon vazifa
    async def poll_once(self) -> None:
        for order in await self.db.orders_to_check():
            await self.check_order(order['id'], notify=True)
            await asyncio.sleep(0.3)
        for purchase in await self.db.due_deliveries():
            if purchase['attempts'] < MAX_DELIVERY_ATTEMPTS:
                await self.deliver(purchase['id'])

    async def run_poller(self, stop: asyncio.Event) -> None:
        log.info('To‘lovlarni davriy tekshirish yoqildi: har %s soniyada', self.settings.payment_check_interval)
        while not stop.is_set():
            try:
                await self.poll_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception('Fon tekshiruvida xato')
            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(stop.wait(), timeout=self.settings.payment_check_interval)

