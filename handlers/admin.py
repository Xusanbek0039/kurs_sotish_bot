"""Telegram ichidagi admin panel: kurslar, buyurtmalar, qidiruv, statistika, tarix.

Router darajasida `IsAdmin` filtri qo‘yilgan: admin bo‘lmagan foydalanuvchi callback’ni qo‘lda
yuborsa ham bu handlerlarga yetib kelmaydi.
"""
from __future__ import annotations

import json
import logging
import re
import sqlite3
from urllib.parse import urlencode

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramAPIError
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

from database import ORDER_STATUSES, Database
from handlers.user import course_view
from handlers.utils import IsAdmin, show
from keyboards import admin as kb
from keyboards.callbacks import AdminCb
from locales import all_variants, t
from services.payments import PaymentService
from services.ui import course_name, dt, h, missing_translations, money, user_label

log = logging.getLogger(__name__)
router = Router(name='admin')
router.message.filter(IsAdmin())
router.callback_query.filter(IsAdmin())

PER_PAGE = 10
TEXT_LIMITS = {'name_uz': (2, 120), 'name_ru': (2, 120), 'description_uz': (10, 3000), 'description_ru': (10, 3000)}
OPTIONAL = {'description_uz', 'name_ru', 'description_ru'}
PRICE_MIN, PRICE_MAX = 1000, 1_000_000_000   # Tezcheck: eng kichik summa 1 000 so‘m


class CourseForm(StatesGroup):
    name_uz = State()
    description_uz = State()
    name_ru = State()
    description_ru = State()
    price_uzs = State()
    channel_chat_id = State()
    confirm = State()


class EditCourse(StatesGroup):
    value = State()


class Search(StatesGroup):
    query = State()


STEPS = ['name_uz', 'description_uz', 'name_ru', 'description_ru', 'price_uzs', 'channel_chat_id']
STEP_PROMPTS = {'name_uz': 'add_name_uz', 'description_uz': 'add_desc_uz', 'name_ru': 'add_name_ru',
                'description_ru': 'add_desc_ru', 'price_uzs': 'add_price', 'channel_chat_id': 'add_channel'}


# ------------------------------------------------------------------ validatsiya
def parse_price(text: str) -> int | None:
    raw = re.sub(r'[\s_.,]', '', text or '')
    if not raw.isascii() or not raw.isdigit():
        return None
    value = int(raw)
    return value if PRICE_MIN <= value <= PRICE_MAX else None


def parse_chat_id(text: str) -> int | None:
    raw = (text or '').strip()
    return int(raw) if re.fullmatch(r'-?\d{5,20}', raw) else None


def validate_text(field: str, text: str) -> str | None:
    low, high = TEXT_LIMITS[field]
    text = (text or '').strip()
    return text if low <= len(text) <= high else None


async def check_channel(bot: Bot, chat_id: int) -> str | None:
    """None — hammasi joyida; aks holda locales’dagi sabab kaliti."""
    try:
        await bot.get_chat(chat_id)
        member = await bot.get_chat_member(chat_id, bot.id)
    except TelegramAPIError:
        return 'chan_not_found'
    if member.status == 'creator':
        return None
    if member.status != 'administrator':
        return 'chan_not_admin'
    if not getattr(member, 'can_invite_users', False):
        return 'chan_no_invite'
    return None


async def deep_link(bot: Bot, course_id: int, kind: str = 'course') -> str:
    """kind='course' — kurs sahifasi; kind='buy' — Start bosilganda to‘g‘ridan-to‘g‘ri to‘lov."""
    me = await bot.me()
    return f'https://t.me/{me.username}?start={kind}_{course_id}'


# ------------------------------------------------------------------ menyu
async def _menu(event, db: Database, lang: str):
    s = await db.stats()
    await show(event, t(lang, 'admin_title', courses=s['courses'], active=s['active_courses'], pending=s['pending'],
                        undelivered=s['undelivered']), kb.menu_kb(lang))


@router.message(Command('admin'))
@router.message(F.text.in_(all_variants('btn_admin')))
async def admin_cmd(message: Message, state: FSMContext, db: Database, lang: str):
    await state.clear()
    await _menu(message, db, lang)


@router.callback_query(AdminCb.filter(F.action == 'menu'))
async def admin_menu_cb(call: CallbackQuery, state: FSMContext, db: Database, lang: str):
    await call.answer()
    await state.clear()
    await _menu(call, db, lang)


@router.callback_query(AdminCb.filter(F.action == 'cancel'))
async def cancel_cb(call: CallbackQuery, state: FSMContext, db: Database, lang: str):
    await state.clear()
    await call.answer(t(lang, 'input_cancelled'))
    await _menu(call, db, lang)


@router.message(Command('cancel'), StateFilter('*'))
async def cancel_cmd(message: Message, state: FSMContext, db: Database, lang: str):
    await state.clear()
    await message.answer(t(lang, 'input_cancelled'))
    await _menu(message, db, lang)


# ------------------------------------------------------------------ kurs qo‘shish
async def _ask_step(event, state: FSMContext, lang: str, step: str):
    await state.set_state(getattr(CourseForm, step))
    text = t(lang, 'add_step', step=STEPS.index(step) + 1) + t(lang, STEP_PROMPTS[step])
    markup = kb.cancel_kb(lang, skip=step in OPTIONAL)
    if isinstance(event, CallbackQuery):
        await event.message.answer(text, reply_markup=markup)
    else:
        await event.answer(text, reply_markup=markup)


async def _next_step(event, state: FSMContext, lang: str, current: str, bot: Bot):
    index = STEPS.index(current) + 1
    if index < len(STEPS):
        await _ask_step(event, state, lang, STEPS[index])
        return
    await _ask_confirm(event, state, lang)


async def _ask_confirm(event, state: FSMContext, lang: str):
    d = await state.get_data()
    await state.set_state(CourseForm.confirm)
    not_set = t(lang, 'not_set')
    text = t(lang, 'add_confirm', name_uz=h(d.get('name_uz') or not_set), description_uz=h(d.get('description_uz') or not_set),
             name_ru=h(d.get('name_ru') or not_set), description_ru=h(d.get('description_ru') or not_set),
             price=money(d['price_uzs'], lang), channel=d['channel_chat_id'])
    target = event.message if isinstance(event, CallbackQuery) else event
    await target.answer(text, reply_markup=kb.confirm_course_kb(lang))


@router.callback_query(AdminCb.filter(F.action == 'add'))
async def add_start(call: CallbackQuery, state: FSMContext, lang: str):
    await call.answer()
    await state.clear()
    await _ask_step(call, state, lang, 'name_uz')


@router.message(StateFilter(CourseForm.name_uz, CourseForm.description_uz, CourseForm.name_ru,
                            CourseForm.description_ru, CourseForm.price_uzs, CourseForm.channel_chat_id), F.text)
async def add_input(message: Message, state: FSMContext, lang: str, bot: Bot):
    step = (await state.get_state()).split(':')[1]
    text = message.text
    if step in TEXT_LIMITS:
        value = validate_text(step, text)
        if value is None:
            low, high = TEXT_LIMITS[step]
            await message.answer(t(lang, 'invalid_length', min=low, max=high), reply_markup=kb.cancel_kb(lang, step in OPTIONAL))
            return
    elif step == 'price_uzs':
        value = parse_price(text)
        if value is None:
            await message.answer(t(lang, 'invalid_price'), reply_markup=kb.cancel_kb(lang))
            return
    else:
        value = parse_chat_id(text)
        if value is None:
            await message.answer(t(lang, 'invalid_channel'), reply_markup=kb.cancel_kb(lang))
            return
        problem = await check_channel(bot, value)
        if problem:
            await state.update_data(pending_channel=value)
            await message.answer(t(lang, 'channel_problem', reason=t(lang, problem)), reply_markup=kb.channel_problem_kb(lang))
            return
    await state.update_data({step: value})
    await _next_step(message, state, lang, step, bot)


@router.callback_query(AdminCb.filter(F.action == 'skip'), StateFilter(CourseForm.description_uz, CourseForm.name_ru,
                                                                      CourseForm.description_ru))
async def add_skip(call: CallbackQuery, state: FSMContext, lang: str, bot: Bot):
    await call.answer()
    step = (await state.get_state()).split(':')[1]
    await state.update_data({step: ''})
    await _next_step(call, state, lang, step, bot)


@router.callback_query(AdminCb.filter(F.action == 'chanok'), StateFilter(CourseForm.channel_chat_id))
async def add_channel_anyway(call: CallbackQuery, state: FSMContext, lang: str):
    await call.answer()
    data = await state.get_data()
    if 'pending_channel' not in data:
        await call.message.answer(t(lang, 'session_expired'))
        return
    await state.update_data(channel_chat_id=data['pending_channel'])
    await _ask_confirm(call, state, lang)


@router.callback_query(AdminCb.filter(F.action == 'save'), StateFilter(CourseForm.confirm))
async def add_save(call: CallbackQuery, state: FSMContext, db: Database, lang: str, bot: Bot):
    data = await state.get_data()
    if not data.get('name_uz') and not data.get('name_ru'):
        await call.answer(t(lang, 'need_one_name'), show_alert=True)
        return
    await call.answer()
    course_id = await db.add_course(call.from_user.id, data)
    await state.clear()
    await call.message.answer(t(lang, 'course_saved', course_id=course_id, link=await deep_link(bot, course_id),
                                buy_link=await deep_link(bot, course_id, 'buy')))
    await _course_card(call.message, db, lang, course_id, bot, new=True)


@router.callback_query(AdminCb.filter(F.action.in_({'save', 'skip', 'chanok'})))
async def stale_form_button(call: CallbackQuery, lang: str):
    await call.answer(t(lang, 'session_expired'), show_alert=True)


# ------------------------------------------------------------------ kurslar
@router.callback_query(AdminCb.filter(F.action == 'courses'))
async def courses_cb(call: CallbackQuery, callback_data: AdminCb, db: Database, lang: str):
    await call.answer()
    page = max(0, callback_data.page)
    courses, total = await db.list_courses(page, PER_PAGE, active_only=False)
    text = t(lang, 'admin_courses_title', total=total) if total else t(lang, 'admin_courses_empty')
    await show(call, text, kb.courses_kb(lang, courses, page, total, PER_PAGE))


async def _course_card(event, db: Database, lang: str, course_id: int, bot: Bot, new: bool = False):
    course = await db.get_course(course_id)
    if not course:
        await show(event, t(lang, 'course_not_found'), kb.simple_back_kb(lang, 'courses'))
        return
    counts = await db.one("SELECT COUNT(*) AS n, SUM(status = 'paid') AS paid FROM orders WHERE course_id = ?",
                          (course_id,))
    not_set = t(lang, 'not_set')
    text = t(lang, 'admin_course_card',
             status=t(lang, 'course_active' if course['is_active'] else 'course_inactive'), id=course_id,
             name=h(course_name(course, lang)), name_uz=h(course['name_uz'] or not_set),
             description_uz=h(course['description_uz'] or not_set), name_ru=h(course['name_ru'] or not_set),
             description_ru=h(course['description_ru'] or not_set), price=money(course['price_uzs'], lang),
             channel=course['channel_chat_id'], orders=counts['n'] or 0, paid=counts['paid'] or 0,
             created=dt(course['created_at']), link=await deep_link(bot, course_id),
             buy_link=await deep_link(bot, course_id, 'buy'))
    missing = missing_translations(course)
    if missing:
        text += t(lang, 'missing_translation', fields=', '.join(t(lang, f'field_{f}') for f in missing))
    markup = kb.course_kb(lang, course)
    if new:
        await event.answer(text, reply_markup=markup)
    else:
        await show(event, text, markup)


@router.callback_query(AdminCb.filter(F.action == 'course'))
async def course_cb(call: CallbackQuery, callback_data: AdminCb, state: FSMContext, db: Database, lang: str, bot: Bot):
    await call.answer()
    await state.clear()
    await _course_card(call, db, lang, callback_data.id, bot)


@router.callback_query(AdminCb.filter(F.action == 'preview'))
async def preview_cb(call: CallbackQuery, callback_data: AdminCb, db: Database, lang: str, settings):
    await call.answer()
    text, markup = await course_view(db, callback_data.id, call.from_user.id, lang, settings, is_admin=True)
    await call.message.answer(text, reply_markup=markup)


@router.callback_query(AdminCb.filter(F.action == 'links'))
async def links_cb(call: CallbackQuery, callback_data: AdminCb, db: Database, lang: str, bot: Bot):
    course = await db.get_course(callback_data.id)
    if not course:
        await call.answer(t(lang, 'course_not_found'), show_alert=True)
        return
    await call.answer()
    buy_link = await deep_link(bot, course['id'], 'buy')
    share = 'https://t.me/share/url?' + urlencode({'url': buy_link, 'text': course_name(course, lang)})
    await call.message.answer(t(lang, 'course_links', name=h(course_name(course, lang)), buy_link=buy_link,
                                link=await deep_link(bot, course['id'])),
                              reply_markup=kb.links_kb(lang, course['id'], share))


@router.callback_query(AdminCb.filter(F.action == 'toggle'))
async def toggle_cb(call: CallbackQuery, callback_data: AdminCb, db: Database, lang: str, bot: Bot):
    active = await db.toggle_course(call.from_user.id, callback_data.id)
    if active is None:
        await call.answer(t(lang, 'course_not_found'), show_alert=True)
        return
    await call.answer(t(lang, 'course_toggled_on' if active else 'course_toggled_off'), show_alert=True)
    await _course_card(call, db, lang, callback_data.id, bot)


@router.callback_query(AdminCb.filter(F.action == 'chkchan'))
async def check_channel_cb(call: CallbackQuery, callback_data: AdminCb, db: Database, lang: str, bot: Bot):
    course = await db.get_course(callback_data.id)
    if not course:
        await call.answer(t(lang, 'course_not_found'), show_alert=True)
        return
    problem = await check_channel(bot, course['channel_chat_id'])
    await call.answer(t(lang, 'chan_ok') if not problem else '⚠️ ' + t(lang, problem), show_alert=True)


@router.callback_query(AdminCb.filter(F.action == 'del'))
async def delete_ask(call: CallbackQuery, callback_data: AdminCb, lang: str):
    await call.answer()
    await show(call, t(lang, 'confirm_delete', id=callback_data.id), kb.confirm_delete_kb(lang, callback_data.id))


@router.callback_query(AdminCb.filter(F.action == 'delok'))
async def delete_ok(call: CallbackQuery, callback_data: AdminCb, db: Database, lang: str):
    deleted = await db.delete_course(call.from_user.id, callback_data.id)
    await call.answer(t(lang, 'course_deleted' if deleted else 'course_not_found'), show_alert=True)
    courses, total = await db.list_courses(0, PER_PAGE, active_only=False)
    await show(call, t(lang, 'admin_courses_title', total=total) if total else t(lang, 'admin_courses_empty'),
               kb.courses_kb(lang, courses, 0, total, PER_PAGE))


# ------------------------------------------------------------------ tahrirlash
@router.callback_query(AdminCb.filter(F.action == 'edit'))
async def edit_start(call: CallbackQuery, callback_data: AdminCb, state: FSMContext, db: Database, lang: str):
    field = callback_data.arg
    course = await db.get_course(callback_data.id)
    if field not in kb.EDITABLE or not course:
        await call.answer(t(lang, 'outdated_button'), show_alert=True)
        return
    await call.answer()
    await state.clear()
    await state.set_state(EditCourse.value)
    await state.update_data(course_id=course['id'], field=field)
    current = course[field]
    shown = money(current, lang) if field == 'price_uzs' else (h(current) if current not in ('', None) else t(lang, 'not_set'))
    hint = '\n\n<i>“-” — tozalash / очистить</i>' if field in OPTIONAL else ''
    await call.message.answer(t(lang, 'edit_prompt', field=t(lang, f'field_{field}'), current=shown) + hint,
                              reply_markup=kb.cancel_kb(lang))


async def _save_edit(event, state: FSMContext, db: Database, lang: str, bot: Bot, course_id: int, field: str, value):
    try:
        ok = await db.update_course(event.from_user.id, course_id, field, value)
    except sqlite3.IntegrityError:
        target = event.message if isinstance(event, CallbackQuery) else event
        await target.answer(t(lang, 'need_one_name'))
        return
    await state.clear()
    target = event.message if isinstance(event, CallbackQuery) else event
    await target.answer(t(lang, 'edit_saved') if ok else t(lang, 'course_not_found'))
    await _course_card(target, db, lang, course_id, bot, new=True)


@router.message(StateFilter(EditCourse.value), F.text)
async def edit_input(message: Message, state: FSMContext, db: Database, lang: str, bot: Bot):
    data = await state.get_data()
    field, course_id = data.get('field'), data.get('course_id')
    if field not in kb.EDITABLE or not course_id:
        await state.clear()
        await message.answer(t(lang, 'session_expired'))
        return
    text = message.text.strip()
    if field in TEXT_LIMITS:
        if text == '-' and field in OPTIONAL:
            value = ''
        else:
            value = validate_text(field, text)
            if value is None:
                low, high = TEXT_LIMITS[field]
                await message.answer(t(lang, 'invalid_length', min=low, max=high), reply_markup=kb.cancel_kb(lang))
                return
    elif field == 'price_uzs':
        value = parse_price(text)
        if value is None:
            await message.answer(t(lang, 'invalid_price'), reply_markup=kb.cancel_kb(lang))
            return
    else:
        value = parse_chat_id(text)
        if value is None:
            await message.answer(t(lang, 'invalid_channel'), reply_markup=kb.cancel_kb(lang))
            return
        problem = await check_channel(bot, value)
        if problem:
            await state.update_data(pending_channel=value)
            await message.answer(t(lang, 'channel_problem', reason=t(lang, problem)), reply_markup=kb.channel_problem_kb(lang))
            return
    await _save_edit(message, state, db, lang, bot, course_id, field, value)


@router.callback_query(AdminCb.filter(F.action == 'chanok'), StateFilter(EditCourse.value))
async def edit_channel_anyway(call: CallbackQuery, state: FSMContext, db: Database, lang: str, bot: Bot):
    await call.answer()
    data = await state.get_data()
    if data.get('field') != 'channel_chat_id' or 'pending_channel' not in data:
        await call.message.answer(t(lang, 'session_expired'))
        return
    await _save_edit(call, state, db, lang, bot, data['course_id'], 'channel_chat_id', data['pending_channel'])


# ------------------------------------------------------------------ buyurtmalar
@router.callback_query(AdminCb.filter(F.action == 'orders'))
async def orders_cb(call: CallbackQuery, callback_data: AdminCb, db: Database, lang: str):
    await call.answer()
    status = callback_data.arg if callback_data.arg in ORDER_STATUSES else ''
    page = max(0, callback_data.page)
    rows, total = await db.list_orders(status or None, page, PER_PAGE, course_id=callback_data.id or None)
    label = t(lang, f'status_{status}') if status else t(lang, 'status_all')
    if callback_data.id:
        label += f' · #{callback_data.id}'
    text = t(lang, 'admin_orders_title', filter=label, total=total)
    if not rows:
        text += '\n\n' + t(lang, 'admin_orders_empty')
    await show(call, text, kb.orders_kb(lang, rows, status, page, total, PER_PAGE, callback_data.id))


async def _order_card(event, db: Database, lang: str, order_id: int):
    order = await db.get_order(order_id)
    if not order:
        await show(event, t(lang, 'result_not_found'), kb.simple_back_kb(lang, 'orders'))
        return
    purchase = await db.purchase_by_order(order_id)
    user = await db.get_user(order['user_id']) or {}
    course = await db.get_course(order['course_id'], include_deleted=True)
    if not purchase:
        delivery = t(lang, 'delivery_none')
    elif purchase['status'] == 'sent':
        delivery = t(lang, 'delivery_sent', at=dt(purchase['invite_sent_at']))
    else:
        delivery = t(lang, 'delivery_pending', status=h(purchase['status'] + (
            f' — {purchase["last_error"]}' if purchase['last_error'] else '')))
    text = t(lang, 'admin_order_card', id=order['id'], user_id=order['user_id'], user=user_label(user) if user else '',
             course_id=order['course_id'], course=h(course_name(course, lang) if course else order['course_title']),
             amount=money(order['amount_uzs'], lang), status=t(lang, f'status_{order["status"]}'),
             reference=h(order['external_reference']), bill=h(order['tezcheck_bill_id'] or '—'),
             payment=h(order['tezcheck_payment_id'] or '—'), created=dt(order['created_at']),
             paid_at=dt(order['paid_at']), checked=dt(order['last_checked_at']), error=h(order['last_error'] or '—'),
             delivery=delivery)
    await show(event, text, kb.order_kb(lang, order, purchase))


@router.callback_query(AdminCb.filter(F.action == 'order'))
async def order_cb(call: CallbackQuery, callback_data: AdminCb, db: Database, lang: str):
    await call.answer()
    await _order_card(call, db, lang, callback_data.id)


@router.callback_query(AdminCb.filter(F.action == 'ocheck'))
async def order_check_cb(call: CallbackQuery, callback_data: AdminCb, db: Database, lang: str,
                         payments: PaymentService):
    result = await payments.check_order(callback_data.id)
    await db.log_admin(call.from_user.id, 'order_check', {'order_id': callback_data.id, 'result': result})
    await call.answer(t(lang, 'order_check_result', result=t(lang, f'result_{result}')).replace('<b>', '').replace('</b>', ''),
                      show_alert=True)
    await _order_card(call, db, lang, callback_data.id)


@router.callback_query(AdminCb.filter(F.action == 'resend'))
async def resend_cb(call: CallbackQuery, callback_data: AdminCb, db: Database, lang: str, payments: PaymentService):
    purchase = await db.get_purchase(callback_data.id)
    if not purchase:
        await call.answer(t(lang, 'result_not_found'), show_alert=True)
        return
    # Admin qayta yuborganda eski havola bekor qilinib, yangi bir martalik havola yaratiladi.
    result = await payments.deliver(purchase['id'], actor='admin', new_link=True)
    await db.log_admin(call.from_user.id, 'resend_link', {'purchase_id': purchase['id'],
                                                          'order_id': purchase['order_id'], 'result': result})
    await call.answer(t(lang, 'resend_result', result=t(lang, f'result_{result}')).replace('<b>', '').replace('</b>', ''),
                      show_alert=True)
    await _order_card(call, db, lang, purchase['order_id'])


@router.callback_query(AdminCb.filter(F.action == 'undeliv'))
async def undelivered_cb(call: CallbackQuery, callback_data: AdminCb, db: Database, lang: str):
    await call.answer()
    page = max(0, callback_data.page)
    rows, total = await db.undelivered(page, PER_PAGE)
    text = t(lang, 'undelivered_title', total=total) if total else t(lang, 'undelivered_empty')
    if rows:
        text += '\n\n' + '\n'.join(t(lang, 'undelivered_line', order_id=p['order_id'], user_id=p['user_id'],
                                     course=h(course_name(p, lang)), status=h(p['status'])) for p in rows)
    await show(call, text, kb.undelivered_kb(lang, rows, page, total, PER_PAGE))


# ------------------------------------------------------------------ qidiruv
@router.callback_query(AdminCb.filter(F.action == 'search'))
async def search_start(call: CallbackQuery, state: FSMContext, lang: str):
    await call.answer()
    await state.set_state(Search.query)
    await call.message.answer(t(lang, 'search_prompt'), reply_markup=kb.cancel_kb(lang))


@router.message(StateFilter(Search.query), F.text)
async def search_input(message: Message, state: FSMContext, db: Database, lang: str):
    query = message.text.strip()[:64]
    await state.clear()
    orders, users = [], []
    if query.lstrip('#').isdigit():
        number = int(query.lstrip('#'))
        order = await db.get_order(number)
        orders = [order] if order else []
        orders += (await db.list_orders(None, 0, 5, user_id=number))[0] if not query.startswith('#') else []
    if not query.startswith('#'):
        users = await db.search_users(query)
    seen, unique_orders = set(), []
    for o in orders:
        if o['id'] not in seen:
            seen.add(o['id'])
            unique_orders.append(o)
    text = t(lang, 'search_results') if unique_orders or users else t(lang, 'search_empty')
    await message.answer(text, reply_markup=kb.search_results_kb(lang, unique_orders[:10], users[:10]))


# ------------------------------------------------------------------ statistika va tarix
@router.callback_query(AdminCb.filter(F.action == 'stats'))
async def stats_cb(call: CallbackQuery, db: Database, lang: str):
    await call.answer()
    s = await db.stats()
    await show(call, t(lang, 'stats', **{**s, 'revenue': money(s['revenue'], lang)}), kb.simple_back_kb(lang))


@router.callback_query(AdminCb.filter(F.action == 'logs'))
async def logs_cb(call: CallbackQuery, callback_data: AdminCb, db: Database, lang: str):
    await call.answer()
    page = max(0, callback_data.page)
    rows, total = await db.admin_logs(page, 15)
    if not rows:
        await show(call, t(lang, 'logs_empty'), kb.simple_back_kb(lang))
        return
    lines = [t(lang, 'logs_title'), '']
    for r in rows:
        details = r['details']
        with_json = details
        try:
            parsed = json.loads(details)
            with_json = ', '.join(f'{k}={v}' for k, v in parsed.items() if k not in ('description_uz', 'description_ru'))
        except (ValueError, AttributeError):
            pass
        lines.append(f'🕒 {dt(r["created_at"])} · 👤 <code>{r["admin_id"]}</code>\n<b>{h(r["action"])}</b> {h(with_json[:150])}')
    await show(call, '\n'.join(lines), kb.logs_kb(lang, page, total, 15))
