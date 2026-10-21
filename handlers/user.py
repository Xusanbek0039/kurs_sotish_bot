"""Xaridor handlerlari: /start, til, katalog, kurs kartasi, to‘lov va “Kurslarim”."""
from __future__ import annotations

import contextlib
import logging
import time

from aiogram import F, Router
from aiogram.exceptions import TelegramAPIError
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from keyboards.callbacks import LangCb, UserCb
from keyboards.common import language_kb, main_reply_kb
from keyboards.user import (CATALOG_PAGE, back_to_menu_kb, catalog_kb, course_kb, main_menu_kb,
                            my_courses_kb, payment_kb)
from locales import LANGUAGE_BUTTON, LANGUAGE_PROMPT, all_variants, t
from services.payments import PaymentService
from services.ui import course_description, course_name, emoji, h, money
from handlers.utils import show

log = logging.getLogger(__name__)
router = Router(name='user')

CHECK_COOLDOWN = 5.0
_last_check: dict[int, float] = {}


def _support(lang: str, settings) -> str:
    if settings.support_username:
        return t(lang, 'support_contact', username=settings.support_username)
    return t(lang, 'support_contact_admin')


# ------------------------------------------------------------------ start va til
@router.message(CommandStart())
async def cmd_start(message: Message, command: CommandObject, state: FSMContext, db, db_user: dict, lang: str,
                    is_admin: bool, settings, payments: PaymentService):
    await state.clear()
    payload = (command.args or '').strip()
    # Avval to‘langan, lekin havolasi yetmay qolgan xaridlarni yetkazishga urinamiz.
    payments.spawn(payments.deliver_pending_for_user(message.from_user.id))
    if not db_user.get('language'):
        await state.update_data(pending_start=payload)
        await message.answer(LANGUAGE_PROMPT, reply_markup=language_kb())
        return
    await _after_start(message, payload, lang, is_admin, settings, message.from_user.first_name, db, payments)


async def _after_start(message: Message, payload: str, lang: str, is_admin: bool, settings,
                       first_name: str | None, db, payments: PaymentService):
    if settings.sticker_welcome and not payload:
        with contextlib.suppress(TelegramAPIError):
            await message.answer_sticker(settings.sticker_welcome)
    await message.answer(t(lang, 'welcome', emoji=emoji(settings, 'course', '✨'), name=h(first_name or '')),
                         reply_markup=main_reply_kb(lang, is_admin))
    kind, _, raw = payload.partition('_')
    if kind in ('course', 'buy') and raw:
        # Deep link: ?start=course_<id> — kurs sahifasi; ?start=buy_<id> — to‘g‘ridan-to‘g‘ri to‘lov.
        if not (raw.isdigit() and len(raw) <= 9) or not await db.get_course(int(raw)):
            await message.answer(t(lang, 'course_not_found'), reply_markup=back_to_menu_kb(lang, settings))
        elif kind == 'buy':
            if not is_admin:
                await db.record_course_view(message.chat.id, int(raw))
            await send_checkout(message, message.chat.id, int(raw), lang, settings, payments)
        else:
            await send_course(message, db, int(raw), lang, settings, message.chat.id, is_admin)
        return
    await message.answer(t(lang, 'main_menu'), reply_markup=main_menu_kb(lang, is_admin, settings))


@router.message(F.text == LANGUAGE_BUTTON)
@router.message(Command('language', 'til', 'lang'))
async def ask_language(message: Message):
    await message.answer(LANGUAGE_PROMPT, reply_markup=language_kb())


@router.callback_query(UserCb.filter(F.action == 'lang'))
async def ask_language_cb(call: CallbackQuery):
    await call.answer()
    await call.message.answer(LANGUAGE_PROMPT, reply_markup=language_kb())


@router.callback_query(LangCb.filter(F.code.in_({'uz', 'ru'})))
async def set_language(call: CallbackQuery, callback_data: LangCb, state: FSMContext, db, is_admin: bool, settings,
                       payments: PaymentService):
    lang = callback_data.code
    # Tilni almashtirish buyurtma, xarid va to‘lov holatiga ta’sir qilmaydi — faqat users.language.
    await db.set_language(call.from_user.id, lang)
    await call.answer(t(lang, 'language_saved'))
    with contextlib.suppress(TelegramAPIError):
        await call.message.delete()
    data = await state.get_data()
    payload = data.get('pending_start')
    if payload is not None:
        await state.update_data(pending_start=None)
        await _after_start(call.message, payload, lang, is_admin, settings, call.from_user.first_name, db, payments)
        return
    await call.message.answer(t(lang, 'language_saved'), reply_markup=main_reply_kb(lang, is_admin))
    await call.message.answer(t(lang, 'main_menu'), reply_markup=main_menu_kb(lang, is_admin, settings))


# ------------------------------------------------------------------ menyu
@router.callback_query(UserCb.filter(F.action == 'menu'))
async def menu_cb(call: CallbackQuery, lang: str, is_admin: bool, settings):
    await call.answer()
    await show(call, t(lang, 'main_menu'), main_menu_kb(lang, is_admin, settings))


@router.message(Command('help'))
@router.message(F.text.in_(all_variants('btn_help')))
async def help_msg(message: Message, lang: str, settings):
    await message.answer(t(lang, 'help', user_id=message.from_user.id, support=_support(lang, settings)),
                         reply_markup=back_to_menu_kb(lang, settings))


@router.callback_query(UserCb.filter(F.action == 'help'))
async def help_cb(call: CallbackQuery, lang: str, settings):
    await call.answer()
    await show(call, t(lang, 'help', user_id=call.from_user.id, support=_support(lang, settings)),
               back_to_menu_kb(lang, settings))


# ------------------------------------------------------------------ katalog
async def _catalog(event, db, lang: str, settings, page: int = 0):
    courses, total = await db.list_courses(page, CATALOG_PAGE)
    if not courses and page:
        courses, total = await db.list_courses(0, CATALOG_PAGE)
        page = 0
    text = t(lang, 'catalog_title', emoji=emoji(settings, 'course', '📚')) if courses else t(lang, 'catalog_empty')
    await show(event, text, catalog_kb(lang, courses, page, total))


@router.message(Command('courses'))
@router.message(F.text.in_(all_variants('btn_courses')))
async def catalog_msg(message: Message, db, lang: str, settings):
    await _catalog(message, db, lang, settings)


@router.callback_query(UserCb.filter(F.action == 'catalog'))
async def catalog_cb(call: CallbackQuery, callback_data: UserCb, db, lang: str, settings):
    await call.answer()
    await _catalog(call, db, lang, settings, max(0, callback_data.page))


async def course_view(db, course_id: int, user_id: int, lang: str, settings, is_admin: bool):
    course = await db.get_course(course_id)
    if not course:
        return t(lang, 'course_not_found'), back_to_menu_kb(lang, settings)
    if not is_admin:
        await db.record_course_view(user_id, course_id)
    owned = await db.purchase_for(user_id, course_id) is not None
    purchasable = bool(course['is_active'])
    text = t(lang, 'course_card', emoji=emoji(settings, 'course', '📚'), name=h(course_name(course, lang)),
             description=h(course_description(course, lang)), price=money(course['price_uzs'], lang))
    if owned:
        text += t(lang, 'course_owned')
    elif not purchasable:
        text += '\n\n' + t(lang, 'course_unavailable')
    return text, course_kb(lang, course_id, owned, purchasable, settings)


async def send_course(message: Message, db, course_id: int, lang: str, settings, user_id: int, is_admin=False):
    text, markup = await course_view(db, course_id, user_id, lang, settings, is_admin)
    await message.answer(text, reply_markup=markup)


@router.callback_query(UserCb.filter(F.action == 'course'))
async def course_cb(call: CallbackQuery, callback_data: UserCb, db, lang: str, settings, is_admin: bool):
    await call.answer()
    text, markup = await course_view(db, callback_data.id, call.from_user.id, lang, settings, is_admin)
    await show(call, text, markup)


# ------------------------------------------------------------------ to‘lov
@router.callback_query(UserCb.filter(F.action == 'buy'))
async def buy_cb(call: CallbackQuery, callback_data: UserCb, lang: str, settings, payments: PaymentService):
    await call.answer(t(lang, 'payment_preparing'))
    await send_checkout(call, call.from_user.id, callback_data.id, lang, settings, payments)


async def send_checkout(event, user_id: int, course_id: int, lang: str, settings, payments: PaymentService):
    """Buyurtma yaratib, to‘lov tugmasini ko‘rsatadi (kurs kartasidagi tugma va buy_<id> havolasi uchun)."""
    result = await payments.checkout(user_id, course_id)
    if result.kind == 'purchased':
        await show(event, t(lang, 'already_purchased'), back_to_menu_kb(lang, settings))
    elif result.kind == 'unavailable':
        await show(event, t(lang, 'course_unavailable'), back_to_menu_kb(lang, settings))
    elif result.kind == 'ready':
        order = result.order
        course = await payments.db.get_course(order['course_id'], include_deleted=True)
        await show(event, t(lang, 'payment_ready', emoji=emoji(settings, 'payment', '💳'),
                            course=h(course_name(course, lang) if course else order['course_title']),
                            order_id=order['id'], amount=money(order['amount_uzs'], lang)),
                   payment_kb(lang, order, settings))
    else:
        await show(event, t(lang, 'payment_error', support=_support(lang, settings)), back_to_menu_kb(lang, settings))


@router.callback_query(UserCb.filter(F.action == 'check'))
async def check_cb(call: CallbackQuery, callback_data: UserCb, lang: str, settings, payments: PaymentService):
    order = await payments.db.get_order(callback_data.id)
    if not order or order['user_id'] != call.from_user.id:
        await call.answer(t(lang, 'outdated_button'), show_alert=True)
        return
    now = time.monotonic()
    if now - _last_check.get(call.from_user.id, 0) < CHECK_COOLDOWN:
        await call.answer(t(lang, 'check_too_often'))
        return
    _last_check[call.from_user.id] = now
    await call.answer('🔄')
    # Faqat server tomonidagi Tezcheck API javobi to‘lovni tasdiqlaydi.
    result = await payments.check_order(order['id'])
    key = {'paid': 'status_check_paid', 'pending': 'status_check_pending', 'expired': 'status_check_expired',
           'test': 'status_check_test', 'review': 'status_check_review', 'no_bill': 'status_check_no_bill'}.get(
        result, 'status_check_error')
    markup = payment_kb(lang, order, settings) if result == 'pending' and order['payment_url'] else back_to_menu_kb(lang, settings)
    await call.message.answer(t(lang, key, support=_support(lang, settings)), reply_markup=markup)


# ------------------------------------------------------------------ kurslarim
async def _my_courses(event, db, lang: str, settings, user_id: int):
    purchases = await db.user_purchases(user_id)
    orders, _ = await db.list_orders('pending', 0, 10, user_id=user_id)
    if not purchases and not orders:
        await show(event, t(lang, 'my_empty'), back_to_menu_kb(lang, settings))
        return
    lines = [t(lang, 'my_title')]
    if purchases:
        lines.append('\n' + t(lang, 'my_purchases'))
        for p in purchases:
            status = 'pstatus_sent' if p['status'] == 'sent' else (
                'pstatus_waiting' if p['status'] in ('pending_invite', 'sending', 'link_created') else 'pstatus_failed')
            lines.append(f'✅ {h(course_name(p, lang))} — {t(lang, status)}')
    if orders:
        lines.append('\n' + t(lang, 'my_orders'))
        for o in orders:
            lines.append(f'⏳ #{o["id"]} · {h(o["course_title"])} · {money(o["amount_uzs"], lang)}')
    await show(event, '\n'.join(lines), my_courses_kb(lang, purchases, orders, settings))


@router.message(Command('mycourses'))
@router.message(F.text.in_(all_variants('btn_my_courses')))
async def my_msg(message: Message, db, lang: str, settings):
    await _my_courses(message, db, lang, settings, message.from_user.id)


@router.callback_query(UserCb.filter(F.action == 'mine'))
async def my_cb(call: CallbackQuery, db, lang: str, settings):
    await call.answer()
    await _my_courses(call, db, lang, settings, call.from_user.id)


@router.callback_query(UserCb.filter(F.action == 'link'))
async def link_cb(call: CallbackQuery, callback_data: UserCb, lang: str, settings, payments: PaymentService):
    purchase = await payments.db.purchase_for(call.from_user.id, callback_data.id)
    if not purchase:
        await call.answer(t(lang, 'outdated_button'), show_alert=True)
        return
    result = await payments.deliver(purchase['id'], actor='user')
    replies = {'sent': 'link_sent', 'member': 'link_member', 'busy': 'link_busy'}
    if result in replies:
        await call.answer(t(lang, replies[result]), show_alert=result != 'sent')
    elif result == 'limit':
        await call.answer()
        await call.message.answer(t(lang, 'link_limit', support=_support(lang, settings)))
    elif result == 'invite_failed':
        await call.answer()  # foydalanuvchi va adminlar allaqachon xabardor qilindi
    else:
        await call.answer()
        await call.message.answer(t(lang, 'link_failed', support=_support(lang, settings)))
