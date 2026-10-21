"""Inline tugmalar uchun callback ma’lumotlari (Telegram cheklovi: 64 bayt)."""
from aiogram.filters.callback_data import CallbackData


class LangCb(CallbackData, prefix='lang'):
    code: str


class UserCb(CallbackData, prefix='u'):
    action: str            # menu | catalog | course | buy | card | check | mine | link | help
    id: int = 0
    page: int = 0


class AdminCb(CallbackData, prefix='adm'):
    action: str
    id: int = 0
    page: int = 0
    arg: str = ''
