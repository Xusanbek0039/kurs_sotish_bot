"""Ko‘rinish yordamchilari: summa, sana, kurs matni va custom emoji."""
from __future__ import annotations

import re
from datetime import timedelta, timezone
from html import escape

from aiogram.client.session.middlewares.base import BaseRequestMiddleware
from aiogram.exceptions import TelegramBadRequest
from aiogram.methods import EditMessageText, SendMessage

from database import parse_ts
from locales import t

TASHKENT = timezone(timedelta(hours=5), 'Asia/Tashkent')
_TG_EMOJI = re.compile(r'<tg-emoji emoji-id="\d+">(.*?)</tg-emoji>', re.S)


def money(amount: int, lang: str) -> str:
    return f'{amount:,}'.replace(',', ' ') + ' ' + t(lang, 'currency')


def dt(value: str | None) -> str:
    moment = parse_ts(value)
    return moment.astimezone(TASHKENT).strftime('%d.%m.%Y %H:%M') if moment else '—'


def emoji(settings, key: str, fallback: str) -> str:
    """Custom emoji sozlangan bo‘lsa, `<tg-emoji>`; aks holda oddiy emoji."""
    emoji_id = settings.custom_emoji.get(key) if settings else None
    return f'<tg-emoji emoji-id="{emoji_id}">{fallback}</tg-emoji>' if emoji_id else fallback


def course_name(course: dict, lang: str) -> str:
    other = 'ru' if lang == 'uz' else 'uz'
    return course.get(f'name_{lang}') or course.get(f'name_{other}') or f'#{course.get("id", "?")}'


def course_description(course: dict, lang: str) -> str:
    other = 'ru' if lang == 'uz' else 'uz'
    return course.get(f'description_{lang}') or course.get(f'description_{other}') or ''


def missing_translations(course: dict) -> list[str]:
    return [f for f in ('name_uz', 'description_uz', 'name_ru', 'description_ru') if not course.get(f)]


def h(text) -> str:
    return escape(str(text if text is not None else ''))


def user_label(user: dict) -> str:
    name = h(user.get('first_name') or '—')
    return f'{name} (@{h(user["username"])})' if user.get('username') else name


class EmojiFallbackMiddleware(BaseRequestMiddleware):
    """Custom emoji ID noto‘g‘ri bo‘lsa, xabar oddiy emoji bilan qayta yuboriladi."""

    async def __call__(self, make_request, bot, method):
        try:
            return await make_request(bot, method)
        except TelegramBadRequest:
            if not isinstance(method, (SendMessage, EditMessageText)):
                raise
            text = method.text if isinstance(method.text, str) else ''
            markup = getattr(method, 'reply_markup', None)
            has_icons = bool(markup and getattr(markup, 'inline_keyboard', None) and any(
                b.icon_custom_emoji_id for row in markup.inline_keyboard for b in row))
            if '<tg-emoji' not in text and not has_icons:
                raise
            update = {'text': _TG_EMOJI.sub(r'\1', text)}
            if has_icons:
                update['reply_markup'] = markup.model_copy(update={'inline_keyboard': [
                    [b.model_copy(update={'icon_custom_emoji_id': None}) for b in row]
                    for row in markup.inline_keyboard]})
            return await make_request(bot, method.model_copy(update=update))
