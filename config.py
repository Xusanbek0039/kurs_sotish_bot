"""`.env` sozlamalarini o‘qish va tekshirish.

Maxfiy qiymatlar faqat shu yerda o‘qiladi va hech qachon logga chiqarilmaydi.
"""
from __future__ import annotations

import logging
import os
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse

from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parent
LANGUAGES = ('uz', 'ru')
EMOJI_KEYS = ('course', 'payment', 'success')

log = logging.getLogger(__name__)


class ConfigError(ValueError):
    """`.env` sozlamalari noto‘g‘ri yoki yetishmayapti."""


@dataclass(frozen=True)
class Settings:
    bot_token: str = field(repr=False)
    admin_ids: frozenset[int]
    tezcheck_api_key: str = field(repr=False)
    tezcheck_cash_desk_code: str = field(repr=False)
    tezcheck_base_url: str = 'https://api.tezcheck.uz/api/merchant/v1'
    database_path: Path = ROOT / 'data' / 'bot.sqlite3'
    support_username: str = ''
    default_language: str = 'uz'
    payment_check_interval: int = 60
    invite_link_ttl_hours: int = 24
    # Ikkalasi ham o‘zgaruvchan lug‘at: admin botga stiker/emoji yuborib, ularni ish paytida almashtiradi.
    custom_emoji: dict[str, str] = field(default_factory=dict)     # course | payment | success
    stickers: dict[str, str] = field(default_factory=dict)         # welcome | success
    card_number: str = ''         # bo‘sh bo‘lsa, karta + chek orqali to‘lov o‘chiq
    card_holder: str = ''

    def secrets(self) -> list[str]:
        """Loglardan yashiriladigan qiymatlar."""
        return [s for s in (self.bot_token, self.tezcheck_api_key) if s]


def _get(env: Mapping[str, str | None], *names: str, default: str = '') -> str:
    """Birinchi to‘ldirilgan nomni qaytaradi (eski nomlar zaxira sifatida)."""
    for name in names:
        value = (env.get(name) or '').strip()
        if value:
            return value
    return default


def _int(env, *names, default: int, low: int, high: int) -> int:
    raw = _get(env, *names, default=str(default))
    try:
        value = int(raw)
    except ValueError:
        raise ConfigError(f'{names[0]} butun son bo‘lishi kerak.') from None
    if not low <= value <= high:
        raise ConfigError(f'{names[0]} {low}–{high} oralig‘ida bo‘lishi kerak.')
    return value


def _https_url(value: str, name: str) -> str:
    parsed = urlparse(value)
    if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password:
        raise ConfigError(f'{name} to‘liq HTTPS manzil bo‘lishi kerak (masalan, https://example.uz).')
    return value.rstrip('/')


def load_settings(env: Mapping[str, str | None] | None = None) -> Settings:
    """`.env` faylini (yoki testda berilgan lug‘atni) o‘qib, tekshirilgan sozlamalarni qaytaradi."""
    if env is None:
        env = {**dotenv_values(ROOT / '.env'), **{k: v for k, v in os.environ.items() if v}}

    token = _get(env, 'BOT_TOKEN')
    if not token:
        raise ConfigError('BOT_TOKEN to‘ldirilmagan. @BotFather’dan olingan tokenni .env fayliga yozing.')
    if not re.fullmatch(r'\d{5,}:[A-Za-z0-9_-]{30,}', token):
        raise ConfigError('BOT_TOKEN formati noto‘g‘ri. @BotFather bergan tokenni to‘liq nusxalang.')

    raw_admins = _get(env, 'ADMIN_IDS')
    try:
        admins = frozenset(int(x) for x in re.split(r'[,\s]+', raw_admins) if x)
    except ValueError:
        raise ConfigError('ADMIN_IDS faqat raqamli Telegram ID’lardan iborat bo‘lsin (vergul bilan).') from None
    if not admins or any(a <= 0 for a in admins):
        raise ConfigError('ADMIN_IDS ga kamida bitta musbat Telegram ID yozing.')

    api_key = _get(env, 'TEZCHECK_API_KEY', 'TEZCHECK_API_TOKEN')
    desk = _get(env, 'TEZCHECK_CASH_DESK_CODE')
    if not api_key or not desk:
        raise ConfigError('TEZCHECK_API_KEY va TEZCHECK_CASH_DESK_CODE to‘ldirilishi shart.')
    if not re.fullmatch(r'[\x21-\x7e]+', desk):
        raise ConfigError('TEZCHECK_CASH_DESK_CODE noto‘g‘ri belgilarga ega.')

    base_url = _https_url(_get(env, 'TEZCHECK_BASE_URL', 'TEZCHECK_API_BASE_URL',
                               default='https://api.tezcheck.uz/api/merchant/v1'), 'TEZCHECK_BASE_URL')

    db_path = Path(_get(env, 'DATABASE_PATH', default='data/bot.sqlite3'))
    if not db_path.is_absolute():
        db_path = ROOT / db_path

    support = _get(env, 'SUPPORT_USERNAME').lstrip('@')
    if support and not re.fullmatch(r'[A-Za-z][A-Za-z0-9_]{3,31}', support):
        raise ConfigError('SUPPORT_USERNAME Telegram username bo‘lsin, masalan @kurs_yordam.')

    language = _get(env, 'DEFAULT_LANGUAGE', default='uz').lower()
    if language not in LANGUAGES:
        raise ConfigError('DEFAULT_LANGUAGE faqat uz yoki ru bo‘lishi mumkin.')

    emoji = {}
    for key in EMOJI_KEYS:
        name = f'CUSTOM_EMOJI_{key.upper()}'
        value = _get(env, name)
        if value and re.fullmatch(r'\d{5,25}', value):
            emoji[key] = value
        elif value:
            # Talabga ko‘ra: noto‘g‘ri ID botni to‘xtatmaydi, oddiy emoji ishlatiladi.
            log.warning('%s noto‘g‘ri (faqat raqam bo‘lishi kerak) — oddiy emoji ishlatiladi.', name)

    card_number = re.sub(r'[\s-]', '', _get(env, 'CARD_NUMBER'))
    if card_number and not re.fullmatch(r'\d{16}', card_number):
        raise ConfigError('CARD_NUMBER 16 xonali karta raqami bo‘lishi kerak (masalan, 8600 1234 5678 9012).')
    if card_number:
        card_number = ' '.join(card_number[i:i + 4] for i in range(0, 16, 4))

    return Settings(
        bot_token=token,
        admin_ids=admins,
        tezcheck_api_key=api_key,
        tezcheck_cash_desk_code=desk,
        tezcheck_base_url=base_url,
        database_path=db_path,
        support_username=support,
        default_language=language,
        payment_check_interval=_int(env, 'PAYMENT_CHECK_INTERVAL', 'POLL_INTERVAL', default=60, low=15, high=3600),
        invite_link_ttl_hours=_int(env, 'INVITE_LINK_TTL_HOURS', 'INVITE_TTL_HOURS', default=24, low=1, high=168),
        custom_emoji=emoji,
        stickers={k: v for k, v in (('welcome', _get(env, 'STICKER_WELCOME_FILE_ID')),
                                    ('success', _get(env, 'STICKER_SUCCESS_FILE_ID'))) if v},
        card_number=card_number,
        card_holder=_get(env, 'CARD_HOLDER')[:64],
    )
