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


# Admin bot ichidan o‘zgartira oladigan bezak kalitlari: kalit → (settings’dagi lug‘at, undagi nom).
APPEARANCE = {
    'sticker_welcome': ('stickers', 'welcome'),
    'sticker_success': ('stickers', 'success'),
    'emoji_course': ('custom_emoji', 'course'),
    'emoji_payment': ('custom_emoji', 'payment'),
    'emoji_success': ('custom_emoji', 'success'),
}


EMOJI_MAP_PREFIX = 'emap_'      # app_settings kaliti: emap_👇 → custom emoji ID


def apply_appearance(settings, overrides: dict[str, str]) -> None:
    """Bazadagi qiymatlarni ishlayotgan sozlamalarga qo‘llaydi (bo‘sh qiymat — o‘chirish)."""
    for key, value in overrides.items():
        if key.startswith(EMOJI_MAP_PREFIX) and len(key) > len(EMOJI_MAP_PREFIX):
            plain = key[len(EMOJI_MAP_PREFIX):]
            if value:
                settings.emoji_map[plain] = value
            else:
                settings.emoji_map.pop(plain, None)
            continue
        if key not in APPEARANCE:
            continue
        attr, name = APPEARANCE[key]
        target = getattr(settings, attr)
        if value:
            target[name] = value
        else:
            target.pop(name, None)


def appearance_value(settings, key: str) -> str:
    attr, name = APPEARANCE[key]
    return getattr(settings, attr).get(name, '')


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


def _html_mode(method) -> bool:
    mode = method.parse_mode            # str, None yoki bot standarti (HTML) uchun Default obyekti
    return mode is not None and (not isinstance(mode, str) or mode.upper() == 'HTML') and not method.entities


def map_emoji_text(text: str, emoji_map: dict[str, str]) -> str:
    """Oddiy emojilarni `<tg-emoji>` bilan almashtiradi; mavjud `<tg-emoji>` bloklariga tegmaydi."""
    parts = re.split(r'(<tg-emoji emoji-id="\d+">.*?</tg-emoji>)', text, flags=re.S)
    ordered = sorted(emoji_map.items(), key=lambda kv: -len(kv[0]))     # uzunroq ketma-ketlik avval (❤️ > ❤)
    for i, (plain, _) in enumerate(ordered):
        # Avval vaqtinchalik belgi: keyingi (qisqaroq) emoji allaqachon qo‘yilgan tegning ichiga tushmasin.
        parts = [p if p.startswith('<tg-emoji') else p.replace(plain, f'\0{i}\0') for p in parts]
    text = ''.join(parts)
    for i, (plain, emoji_id) in enumerate(ordered):
        text = text.replace(f'\0{i}\0', f'<tg-emoji emoji-id="{emoji_id}">{plain}</tg-emoji>')
    return text


def map_emoji_markup(markup, emoji_map: dict[str, str]):
    """Inline tugma matni moslangan emoji bilan boshlansa, uni tugma ikonkasiga aylantiradi."""
    if not getattr(markup, 'inline_keyboard', None):
        return markup
    changed, rows = False, []
    for row in markup.inline_keyboard:
        new_row = []
        for b in row:
            if not b.icon_custom_emoji_id:
                for plain, emoji_id in emoji_map.items():
                    if b.text.startswith(plain + ' '):
                        b = b.model_copy(update={'text': b.text[len(plain) + 1:], 'icon_custom_emoji_id': emoji_id})
                        changed = True
                        break
            new_row.append(b)
        rows.append(new_row)
    return markup.model_copy(update={'inline_keyboard': rows}) if changed else markup


class EmojiFallbackMiddleware(BaseRequestMiddleware):
    """Bot xabarlariga custom emoji qo‘yadi; Telegram rad etsa, xabar oddiy emoji bilan qayta yuboriladi.

    emoji_map: {'👇': '5470…'} — admin “🎨 Bezak” bo‘limidan to‘ldiradi (ish paytida o‘zgaradigan lug‘at).
    """

    def __init__(self, emoji_map: dict[str, str] | None = None):
        self.emoji_map = emoji_map if emoji_map is not None else {}

    async def __call__(self, make_request, bot, method):
        if self.emoji_map and isinstance(method, (SendMessage, EditMessageText)) and _html_mode(method):
            update = {}
            if isinstance(method.text, str):
                mapped = map_emoji_text(method.text, self.emoji_map)
                if mapped != method.text:
                    update['text'] = mapped
            markup = getattr(method, 'reply_markup', None)
            mapped_markup = map_emoji_markup(markup, self.emoji_map)
            if mapped_markup is not markup:
                update['reply_markup'] = mapped_markup
            if update:
                try:
                    return await make_request(bot, method.model_copy(update=update))
                except TelegramBadRequest:
                    pass   # Telegram custom emoji’ni qabul qilmadi — asl xabar bilan pastda davom etamiz
        return await self._send_with_fallback(make_request, bot, method)

    @staticmethod
    async def _send_with_fallback(make_request, bot, method):
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
