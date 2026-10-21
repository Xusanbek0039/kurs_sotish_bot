"""Admin panel klaviaturalari."""
from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from database import ORDER_STATUSES, USER_FILTERS
from keyboards.callbacks import AdminCb, UserCb
from keyboards.common import add_pagination
from locales import LANGUAGE_BUTTON, t
from services.ui import course_name, missing_translations, money

EDITABLE = ('name_uz', 'name_ru', 'description_uz', 'description_ru', 'price_uzs', 'channel_chat_id')


def _b(text: str, action: str, id: int = 0, page: int = 0, arg: str = '', style: str | None = None):
    return InlineKeyboardButton(text=text, callback_data=AdminCb(action=action, id=id, page=page, arg=arg).pack(),
                                style=style)


def back_row(lang: str, action: str = 'menu', id: int = 0, arg: str = '') -> list[InlineKeyboardButton]:
    return [_b(t(lang, 'btn_back'), action, id, arg=arg), _b(t(lang, 'btn_admin_panel'), 'menu')]


def menu_kb(lang: str) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.row(_b(t(lang, 'btn_add_course'), 'add', style='success'))
    b.row(_b(t(lang, 'btn_admin_courses'), 'courses'), _b(t(lang, 'btn_admin_orders'), 'orders'))
    b.row(_b(t(lang, 'btn_admin_users'), 'users'), _b(t(lang, 'btn_admin_search'), 'search'))
    b.row(_b(t(lang, 'btn_admin_stats'), 'stats'), _b(t(lang, 'btn_admin_undelivered'), 'undeliv'))
    b.row(_b(t(lang, 'btn_admin_logs'), 'logs'),
          InlineKeyboardButton(text=LANGUAGE_BUTTON, callback_data=UserCb(action='lang').pack()))
    return b.as_markup()


def cancel_kb(lang: str, skip: bool = False) -> InlineKeyboardMarkup:
    row = [_b(t(lang, 'btn_skip'), 'skip')] if skip else []
    return InlineKeyboardMarkup(inline_keyboard=[row + [_b(t(lang, 'btn_cancel'), 'cancel', style='danger')]])


def confirm_course_kb(lang: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[_b(t(lang, 'btn_save'), 'save', style='success'),
                                                  _b(t(lang, 'btn_cancel'), 'cancel', style='danger')]])


def channel_problem_kb(lang: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[_b(t(lang, 'btn_continue_anyway'), 'chanok')],
                                                 [_b(t(lang, 'btn_cancel'), 'cancel', style='danger')]])


def courses_kb(lang: str, courses: list[dict], page: int, total: int, per_page: int) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    for c in courses:
        mark = '🟢' if c['is_active'] else '⚪️'
        warn = ' ⚠️' if missing_translations(c) else ''
        b.row(_b(f'{mark} #{c["id"]} {course_name(c, lang)[:35]} · {money(c["price_uzs"], lang)}{warn}', 'course', c['id']))
    add_pagination(b, lang, page, total, per_page, lambda p: AdminCb(action='courses', page=p).pack())
    b.row(_b(t(lang, 'btn_add_course'), 'add', style='success'))
    b.row(*back_row(lang))
    return b.as_markup()


def course_kb(lang: str, course: dict) -> InlineKeyboardMarkup:
    cid = course['id']
    b = InlineKeyboardBuilder()
    fields = [_b(t(lang, 'btn_edit_field', field=t(lang, f'field_{f}')), 'edit', cid, arg=f) for f in EDITABLE]
    for i in range(0, len(fields), 2):
        b.row(*fields[i:i + 2])
    b.row(_b(t(lang, 'btn_deactivate') if course['is_active'] else t(lang, 'btn_activate'), 'toggle', cid),
          _b(t(lang, 'btn_check_channel'), 'chkchan', cid))
    b.row(_b(t(lang, 'btn_course_orders'), 'orders', cid), _b(t(lang, 'btn_preview'), 'preview', cid))
    b.row(_b(t(lang, 'btn_delete'), 'del', cid, style='danger'))
    b.row(*back_row(lang, 'courses'))
    return b.as_markup()


def confirm_delete_kb(lang: str, course_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[_b(t(lang, 'btn_confirm_delete'), 'delok', course_id, style='danger'),
                                                  _b(t(lang, 'btn_cancel'), 'course', course_id)]])


def orders_kb(lang: str, orders: list[dict], status: str, page: int, total: int, per_page: int,
              course_id: int = 0) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    filters = [('', t(lang, 'status_all'))] + [(s, t(lang, f'status_{s}')) for s in ORDER_STATUSES]
    buttons = [_b(('• ' if s == status else '') + label, 'orders', course_id, arg=s) for s, label in filters]
    for i in range(0, len(buttons), 3):
        b.row(*buttons[i:i + 3])
    for o in orders:
        b.row(_b(t(lang, 'admin_order_line', id=o['id'], status=t(lang, f'status_{o["status"]}'),
                   amount=money(o['amount_uzs'], lang), user_id=o['user_id']), 'order', o['id']))
    add_pagination(b, lang, page, total, per_page,
                   lambda p: AdminCb(action='orders', id=course_id, page=p, arg=status).pack())
    b.row(*(back_row(lang, 'course', course_id) if course_id else back_row(lang)))
    return b.as_markup()


def order_kb(lang: str, order: dict, purchase: dict | None) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    if order['status'] != 'paid' and order['tezcheck_bill_id']:
        b.row(_b(t(lang, 'btn_order_check'), 'ocheck', order['id'], style='primary'))
    if purchase:
        b.row(_b(t(lang, 'btn_resend_link'), 'resend', purchase['id'], style='success'))
    b.row(_b(t(lang, 'btn_open_user'), 'user', order['user_id']))
    b.row(*back_row(lang, 'orders'))
    return b.as_markup()


def users_kb(lang: str, users: list[dict], flt: str, sort: str, page: int, total: int,
             per_page: int) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    buttons = [_b(('• ' if f == flt else '') + t(lang, f'ufilter_{f}'), 'users', arg=f'{f}.{sort}')
               for f in USER_FILTERS]
    for i in range(0, len(buttons), 2):
        b.row(*buttons[i:i + 2])
    b.row(*[_b(('• ' if s == sort else '') + t(lang, f'usort_{s}'), 'users', arg=f'{flt}.{s}') for s in ('new', 'active')])
    for u in users:
        name = (u.get('first_name') or '—')[:25]
        tag = f' @{u["username"]}' if u.get('username') else ''
        mark = '⛔ ' if u['is_blocked'] else ''
        b.row(_b(f'{mark}{name}{tag} · {u["id"]}', 'user', u['id']))
    add_pagination(b, lang, page, total, per_page, lambda p: AdminCb(action='users', page=p, arg=f'{flt}.{sort}').pack())
    b.row(_b(t(lang, 'btn_users_search'), 'search'), _b(t(lang, 'btn_export_csv'), 'export'))
    b.row(*back_row(lang))
    return b.as_markup()


def user_kb(lang: str, user: dict, is_admin_user: bool) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    for o in user['orders'][:8]:
        b.row(_b(f'🧾 #{o["id"]} · {t(lang, "status_" + o["status"])} · {money(o["amount_uzs"], lang)}', 'order', o['id']))
    for p in user['purchases']:
        if p['status'] != 'sent':
            b.row(_b(f'{t(lang, "btn_resend_link")} · #{p["order_id"]}', 'resend', p['id'], style='success'))
    if not is_admin_user:
        if user['is_blocked']:
            b.row(_b(t(lang, 'btn_unblock'), 'unblock', user['id']))
        else:
            b.row(_b(t(lang, 'btn_block'), 'block', user['id'], style='danger'))
    b.row(*back_row(lang, 'users'))
    return b.as_markup()


def confirm_block_kb(lang: str, user_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[_b(t(lang, 'btn_confirm_block'), 'blockok', user_id, style='danger'),
                                                  _b(t(lang, 'btn_cancel'), 'user', user_id)]])


def undelivered_kb(lang: str, rows: list[dict], page: int, total: int, per_page: int) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    for p in rows:
        b.row(_b(f'📨 #{p["order_id"]} · 👤 {p["user_id"]} · {course_name(p, lang)[:25]}', 'resend', p['id']))
    add_pagination(b, lang, page, total, per_page, lambda p: AdminCb(action='undeliv', page=p).pack())
    b.row(*back_row(lang))
    return b.as_markup()


def search_results_kb(lang: str, orders: list[dict], users: list[dict]) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    for o in orders:
        b.row(_b(t(lang, 'search_order_found', id=o['id']), 'order', o['id']))
    for u in users:
        label = (u.get('first_name') or '—')[:25] + (f' @{u["username"]}' if u.get('username') else '')
        b.row(_b(t(lang, 'search_user_found', label=label, id=u['id']), 'user', u['id']))
    b.row(_b(t(lang, 'btn_admin_search'), 'search'), _b(t(lang, 'btn_admin_panel'), 'menu'))
    return b.as_markup()


def simple_back_kb(lang: str, action: str = 'menu', id: int = 0, page: int = 0, arg: str = '') -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[back_row(lang, action, id, arg)])


def logs_kb(lang: str, page: int, total: int, per_page: int) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    add_pagination(b, lang, page, total, per_page, lambda p: AdminCb(action='logs', page=p).pack())
    b.row(*back_row(lang))
    return b.as_markup()
