"""Umumiy klaviaturalar: til tanlash, pastki menyu, sahifalash."""
from __future__ import annotations

from aiogram.types import (InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton,
                           ReplyKeyboardMarkup)
from aiogram.utils.keyboard import InlineKeyboardBuilder

from keyboards.callbacks import LangCb
from locales import LANGUAGE_BUTTON, TEXTS, t


def language_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text=TEXTS[code]['lang_name'], callback_data=LangCb(code=code).pack())
        for code in ('uz', 'ru')]])


def main_reply_kb(lang: str, is_admin: bool) -> ReplyKeyboardMarkup:
    rows = [
        [KeyboardButton(text=t(lang, 'btn_courses')), KeyboardButton(text=t(lang, 'btn_my_courses'))],
        [KeyboardButton(text=t(lang, 'btn_help')), KeyboardButton(text=LANGUAGE_BUTTON)],
    ]
    if is_admin:
        rows.append([KeyboardButton(text=t(lang, 'btn_admin'))])
    return ReplyKeyboardMarkup(keyboard=rows, resize_keyboard=True, is_persistent=True)


def add_pagination(builder: InlineKeyboardBuilder, lang: str, page: int, total: int, per_page: int, make) -> None:
    """`make(page)` — sahifa uchun callback_data qaytaradigan funksiya."""
    pages = max(1, -(-total // per_page))
    if pages <= 1:
        return
    row = []
    if page > 0:
        row.append(InlineKeyboardButton(text=t(lang, 'btn_prev'), callback_data=make(page - 1)))
    row.append(InlineKeyboardButton(text=f'{page + 1}/{pages}', callback_data=make(page)))
    if page + 1 < pages:
        row.append(InlineKeyboardButton(text=t(lang, 'btn_next'), callback_data=make(page + 1)))
    builder.row(*row)
