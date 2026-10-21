"""Routerlarni ulash. Tartib muhim: admin → foydalanuvchi → zaxira (fallback)."""
from __future__ import annotations

import contextlib
import logging

from aiogram import Dispatcher, F, Router
from aiogram.exceptions import TelegramAPIError
from aiogram.types import CallbackQuery, ErrorEvent, Message

from handlers import admin, admin_users, user
from handlers.utils import UserContextMiddleware
from keyboards.callbacks import AdminCb
from locales import t

log = logging.getLogger(__name__)
fallback = Router(name='fallback')


@fallback.callback_query(AdminCb.filter())
async def admin_denied(call: CallbackQuery, lang: str):
    # Admin bo‘lmagan foydalanuvchi admin callback’ini qo‘lda yuborgan holat.
    log.warning('Ruxsatsiz admin callback urinishi: user=%s', call.from_user.id)
    await call.answer(t(lang, 'no_permission'), show_alert=True)


@fallback.callback_query()
async def stale_button(call: CallbackQuery, lang: str):
    await call.answer(t(lang, 'outdated_button'), show_alert=True)


@fallback.message(F.chat.type == 'private')
async def unknown_message(message: Message, lang: str, is_admin: bool, settings):
    from keyboards.user import main_menu_kb
    await message.answer(t(lang, 'unknown_message'), reply_markup=main_menu_kb(lang, is_admin, settings))


async def on_error(event: ErrorEvent, settings, db) -> bool:
    exc = event.exception
    log.error('Handler xatosi: %s: %s', type(exc).__name__, exc, exc_info=exc)
    update = event.update
    target = update.message or (update.callback_query.message if update.callback_query else None)
    user = update.message.from_user if update.message else (update.callback_query.from_user if update.callback_query else None)
    if target is not None and user is not None:
        with contextlib.suppress(Exception):
            row = await db.get_user(user.id)
            lang = (row or {}).get('language') or settings.default_language
            if update.callback_query:
                await update.callback_query.answer()
            await target.answer(t(lang, 'error_generic'))
    return True


def setup(dp: Dispatcher) -> None:
    middleware = UserContextMiddleware()
    dp.message.outer_middleware(middleware)
    dp.callback_query.outer_middleware(middleware)
    dp.include_routers(admin.router, admin_users.router, user.router, fallback)
    dp.errors.register(on_error)
