"""Handlerlar uchun umumiy filtr, middleware va yordamchilar."""
from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import BaseFilter
from aiogram.types import CallbackQuery, Message, TelegramObject

from locales import t

log = logging.getLogger(__name__)


class IsAdmin(BaseFilter):
    """Admin faqat Telegram ID orqali aniqlanadi (username’ga ishonilmaydi)."""

    async def __call__(self, event: TelegramObject, settings) -> bool:
        user = getattr(event, 'from_user', None)
        return bool(user and user.id in settings.admin_ids)


class UserContextMiddleware(BaseMiddleware):
    """Har bir xabar/tugma bosilishida: foydalanuvchini saqlash, oxirgi faollik, til va blok tekshiruvi."""

    async def __call__(self, handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
                       event: TelegramObject, data: dict[str, Any]) -> Any:
        user = getattr(event, 'from_user', None)
        chat = event.chat if isinstance(event, Message) else (
            event.message.chat if isinstance(event, CallbackQuery) and event.message else None)
        if user is None or user.is_bot or (chat is not None and chat.type != 'private'):
            return None  # bot faqat shaxsiy chatda ishlaydi
        settings, db = data['settings'], data['db']
        row = await db.touch_user(user.id, user.username, user.first_name)
        is_admin = user.id in settings.admin_ids
        lang = row.get('language') or settings.default_language
        data.update(db_user=row, lang=lang, is_admin=is_admin)
        if row['is_blocked'] and not is_admin:
            if isinstance(event, CallbackQuery):
                await event.answer(t(lang, 'blocked'), show_alert=True)
            else:
                await event.answer(t(lang, 'blocked'))
            return None
        return await handler(event, data)


async def show(event: Message | CallbackQuery, text: str, markup=None) -> None:
    """Tugmadan kelgan bo‘lsa — xabarni tahrirlaydi (toza interfeys), aks holda yangi xabar yuboradi."""
    if isinstance(event, CallbackQuery):
        message = event.message
        if isinstance(message, Message) and message.text is not None:
            try:
                await message.edit_text(text, reply_markup=markup)
                return
            except TelegramBadRequest as exc:
                if 'message is not modified' in str(exc):
                    return
        await message.answer(text, reply_markup=markup)
        return
    await event.answer(text, reply_markup=markup)


def target(event: Message | CallbackQuery) -> Message:
    return event.message if isinstance(event, CallbackQuery) else event
