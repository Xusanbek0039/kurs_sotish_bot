import os
import re
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
    mongo_uri: str
    interval: int
    invite_hours: int
    emoji: str
    brand: str = 'Kurslar markazi'
    welcome: str = 'Maqsadingizga mos kursni tanlang. Dastur, narx va kirish tartibi bilan tanishib, qaror qiling.'
    support_username: str = ''

    @classmethod
    def load(cls):
        load_dotenv(ROOT / '.env')
        required = ['BOT_TOKEN', 'ADMIN_IDS', 'TEZCHECK_CASH_DESK_CODE', 'MONGODB_URI']
        if any(not os.getenv(k, '').strip() for k in required):
            raise ValueError('.env ichida BOT_TOKEN, ADMIN_IDS, MONGODB_URI va Tezcheck sozlamalarini to‘ldiring.')
        api_key = os.getenv('TEZCHECK_API_TOKEN', '').strip() or os.getenv('TEZCHECK_API_KEY', '').strip()
        if not api_key:
            raise ValueError('.env ichida TEZCHECK_API_TOKEN ni to‘ldiring (eski TEZCHECK_API_KEY ham ishlaydi).')
        mongo_uri = os.environ['MONGODB_URI'].strip()
        if not mongo_uri.startswith(('mongodb+srv://', 'mongodb://')):
            raise ValueError('MONGODB_URI MongoDB ulanish manzili bo‘lishi kerak.')
        if '<' in mongo_uri or '>' in mongo_uri:
            raise ValueError('MONGODB_URI ichidagi <db_username>/<db_password> o‘rniga haqiqiy ma’lumotlarni yozing.')
        database = os.getenv('MONGODB_DATABASE', 'kurs_sotish_bot').strip()
        if not database or any(c in database for c in '/\\. "$*<>:|?'):
            raise ValueError('MONGODB_DATABASE nomi noto‘g‘ri.')
        admins = frozenset(int(x.strip()) for x in os.environ['ADMIN_IDS'].split(',') if x.strip())
        if not admins or any(x <= 0 for x in admins):
            raise ValueError('ADMIN_IDS musbat Telegram IDlardan iborat bo‘lsin.')
        base = (os.getenv('TEZCHECK_API_BASE_URL', '').strip() or os.getenv('TEZCHECK_BASE_URL', '').strip()
                or 'https://api.tezcheck.uz/api/merchant/v1').rstrip('/')
        if urlparse(base).scheme != 'https' or not urlparse(base).hostname:
            raise ValueError('TEZCHECK_API_BASE_URL HTTPS manzil bo‘lishi kerak.')
        brand = os.getenv('BRAND_NAME', '').strip() or 'Kurslar markazi'
        welcome = os.getenv('WELCOME_TEXT', '').strip() or cls.__dataclass_fields__['welcome'].default
        support = os.getenv('SUPPORT_USERNAME', '').strip().lstrip('@')
        if len(brand) > 80 or len(welcome) > 600:
            raise ValueError('BRAND_NAME 80, WELCOME_TEXT 600 belgidan oshmasin.')
        if support and not re.fullmatch(r'[A-Za-z][A-Za-z0-9_]{4,31}', support):
            raise ValueError('SUPPORT_USERNAME: Telegram username kiriting, masalan @kurs_yordam.')
        emoji = os.getenv('CUSTOM_EMOJI_ID', '').strip()
        if emoji and not emoji.isdigit():
            raise ValueError('CUSTOM_EMOJI_ID raqamlardan iborat yoki bo‘sh bo‘lsin.')
        interval = int(os.getenv('POLL_INTERVAL', '60'))
        hours = int(os.getenv('INVITE_TTL_HOURS', '24'))
        if interval < 30 or not 1 <= hours <= 168:
            raise ValueError('POLL_INTERVAL >= 30, INVITE_TTL_HOURS 1–168 bo‘lsin.')
        return cls(os.environ['BOT_TOKEN'].strip(), admins, api_key,
                   os.environ['TEZCHECK_CASH_DESK_CODE'].strip(), base,
                   database, mongo_uri, interval, hours, emoji, brand, welcome, support)
