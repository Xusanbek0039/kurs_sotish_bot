import os
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent


@dataclass(frozen=True)
class Config:
    token: str
    admins: frozenset[int]
    api_key: str
    desk: str
    base_url: str
    database: str
    interval: int
    invite_hours: int
    emoji: str

    @classmethod
    def load(cls):
        load_dotenv(ROOT / '.env')
        required = ['BOT_TOKEN', 'ADMIN_IDS', 'TEZCHECK_API_KEY', 'TEZCHECK_CASH_DESK_CODE']
        if any(not os.getenv(k, '').strip() for k in required):
            raise ValueError('.env ichida BOT_TOKEN, ADMIN_IDS va Tezcheck sozlamalarini to‘ldiring.')
        admins = frozenset(int(x.strip()) for x in os.environ['ADMIN_IDS'].split(',') if x.strip())
        if not admins or any(x <= 0 for x in admins):
            raise ValueError('ADMIN_IDS musbat Telegram IDlardan iborat bo‘lsin.')
        base = os.getenv('TEZCHECK_BASE_URL', 'https://api.tezcheck.uz/api/merchant/v1').rstrip('/')
        if urlparse(base).scheme != 'https' or not urlparse(base).hostname:
            raise ValueError('TEZCHECK_BASE_URL HTTPS manzil bo‘lishi kerak.')
        emoji = os.getenv('CUSTOM_EMOJI_ID', '').strip()
        if emoji and not emoji.isdigit():
            raise ValueError('CUSTOM_EMOJI_ID raqamlardan iborat yoki bo‘sh bo‘lsin.')
        interval = int(os.getenv('POLL_INTERVAL', '60'))
        hours = int(os.getenv('INVITE_TTL_HOURS', '24'))
        if interval < 30 or not 1 <= hours <= 168:
            raise ValueError('POLL_INTERVAL >= 30, INVITE_TTL_HOURS 1–168 bo‘lsin.')
        return cls(os.environ['BOT_TOKEN'].strip(), admins, os.environ['TEZCHECK_API_KEY'].strip(),
                   os.environ['TEZCHECK_CASH_DESK_CODE'].strip(), base,
                   str(ROOT / os.getenv('DATABASE_PATH', 'data/bot.db')), interval, hours, emoji)
