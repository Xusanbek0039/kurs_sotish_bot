"""Admin: “👥 Foydalanuvchilar” bo‘limi — ro‘yxat, filtr, profil, bloklash va CSV eksport."""
from __future__ import annotations

import csv
import io

from aiogram import F, Router
from aiogram.types import BufferedInputFile, CallbackQuery

from database import USER_FILTERS, Database, utcnow
from handlers.utils import IsAdmin, show
from keyboards import admin as kb
from keyboards.callbacks import AdminCb
from locales import TEXTS, t
from services.ui import course_name, dt, h, money, user_label

router = Router(name='admin_users')
router.message.filter(IsAdmin())
router.callback_query.filter(IsAdmin())

PER_PAGE = 10


def _parse_arg(arg: str) -> tuple[str, str]:
    flt, _, sort = (arg or 'all.new').partition('.')
    return (flt if flt in USER_FILTERS else 'all'), (sort if sort in ('new', 'active') else 'new')


@router.callback_query(AdminCb.filter(F.action == 'users'))
async def users_cb(call: CallbackQuery, callback_data: AdminCb, db: Database, lang: str):
    await call.answer()
    flt, sort = _parse_arg(callback_data.arg)
    page = max(0, callback_data.page)
    rows, total = await db.list_users(flt, sort, page, PER_PAGE)
    summary = {k: (v or 0) for k, v in (await db.users_summary()).items()}
    pages = max(1, -(-total // PER_PAGE))
    text = t(lang, 'users_title', **summary, filter=t(lang, f'ufilter_{flt}'), sort=t(lang, f'usort_{sort}'),
             page_info=t(lang, 'page', page=min(page + 1, pages), pages=pages))
    await show(call, text, kb.users_kb(lang, rows, flt, sort, page, total, PER_PAGE))


async def _profile(event, db: Database, lang: str, user_id: int, settings):
    user = await db.user_profile(user_id)
    if not user:
        await show(event, t(lang, 'user_not_found'), kb.simple_back_kb(lang, 'users'))
        return
    none = t(lang, 'none')
    views = '\n'.join(f'• {h(course_name(v, lang))} — {v["views"]}× ({dt(v["last_viewed_at"])})'
                      for v in user['views']) or none
    purchases = '\n'.join(
        f'• {h(course_name(p, lang))} · #{p["order_id"]} · '
        + (f'✅ {dt(p["invite_sent_at"])}' if p['status'] == 'sent' else f'⏳ {h(p["status"])}')
        for p in user['purchases'][:15]) or none
    orders = '\n'.join(f'• #{o["id"]} · {h(o["course_title"][:30])} · {money(o["amount_uzs"], lang)} · '
                       f'{t(lang, "status_" + o["status"])}' for o in user['orders']) or none
    language = TEXTS[user['language']]['lang_name'] if user['language'] else none
    text = t(lang, 'user_card', label=user_label(user), id=user['id'], language=language,
             created=dt(user['created_at']), last_active=dt(user['last_active_at']),
             blocked=t(lang, 'user_blocked_mark' if user['is_blocked'] else 'user_active_mark'),
             views=views, purchases=purchases, orders=orders)
    await show(event, text, kb.user_kb(lang, user, user['id'] in settings.admin_ids))


@router.callback_query(AdminCb.filter(F.action == 'user'))
async def user_cb(call: CallbackQuery, callback_data: AdminCb, db: Database, lang: str, settings):
    await call.answer()
    await _profile(call, db, lang, callback_data.id, settings)


@router.callback_query(AdminCb.filter(F.action == 'block'))
async def block_ask(call: CallbackQuery, callback_data: AdminCb, db: Database, lang: str, settings):
    if callback_data.id in settings.admin_ids:
        await call.answer(t(lang, 'cannot_block_admin'), show_alert=True)
        return
    user = await db.get_user(callback_data.id)
    if not user:
        await call.answer(t(lang, 'user_not_found'), show_alert=True)
        return
    await call.answer()
    await show(call, t(lang, 'confirm_block', label=user_label(user), id=user['id']),
               kb.confirm_block_kb(lang, user['id']))


@router.callback_query(AdminCb.filter(F.action.in_({'blockok', 'unblock'})))
async def block_set(call: CallbackQuery, callback_data: AdminCb, db: Database, lang: str, settings):
    blocked = callback_data.action == 'blockok'
    if blocked and callback_data.id in settings.admin_ids:
        await call.answer(t(lang, 'cannot_block_admin'), show_alert=True)
        return
    ok = await db.set_blocked(call.from_user.id, callback_data.id, blocked)
    await call.answer(t(lang, ('user_blocked' if blocked else 'user_unblocked') if ok else 'user_not_found'),
                      show_alert=True)
    await _profile(call, db, lang, callback_data.id, settings)


def _cell(value) -> str:
    """CSV/Excel formula injection’dan himoya."""
    text = '' if value is None else str(value)
    return "'" + text if text[:1] in ('=', '+', '-', '@', '\t', '\r') else text


@router.callback_query(AdminCb.filter(F.action == 'export'))
async def export_cb(call: CallbackQuery, db: Database, lang: str):
    await call.answer()
    rows = await db.export_users()
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(['telegram_id', 'username', 'first_name', 'language', 'registered_at_utc', 'last_active_at_utc',
                     'is_blocked', 'orders', 'paid_orders'])
    for r in rows:
        writer.writerow([_cell(r[k]) for k in ('id', 'username', 'first_name', 'language', 'created_at',
                                                'last_active_at', 'is_blocked', 'orders', 'paid_orders')])
    data = buffer.getvalue().encode('utf-8-sig')   # Excel o‘zbek/rus harflarini to‘g‘ri ochishi uchun
    await db.log_admin(call.from_user.id, 'users_export', {'count': len(rows)})
    name = f'users_{utcnow().strftime("%Y%m%d_%H%M")}.csv'
    await call.message.answer_document(BufferedInputFile(data, filename=name),
                                       caption=t(lang, 'export_caption', count=len(rows)))
