"""Bezak: admin botga stiker yoki custom emoji yuboradi — bot ID’ni o‘zi aniqlaydi va qayerga qo‘yishni so‘raydi.

`.env`ni tahrirlash va botni qayta ishga tushirish shart emas: qiymat bazaga yoziladi va darhol qo‘llanadi.
Custom emoji’ni “hamma joyda” qo‘yish mumkin: masalan, har bir bot xabaridagi 👇 sizning custom 👇 bilan almashadi.
"""
from __future__ import annotations

import contextlib
import logging

from aiogram import F, Router
from aiogram.exceptions import TelegramAPIError
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from database import Database
from handlers.utils import IsAdmin, show
from keyboards.callbacks import AdminCb
from locales import t
from services.ui import APPEARANCE, EMOJI_MAP_PREFIX, apply_appearance, appearance_value, h

log = logging.getLogger(__name__)
router = Router(name='admin_appearance')
router.message.filter(IsAdmin())
router.callback_query.filter(IsAdmin())

STICKER_KEYS = ('sticker_welcome', 'sticker_success')
EMOJI_KEYS = ('emoji_course', 'emoji_payment', 'emoji_success')
MAX_PLAIN_LEN = 16      # oddiy emoji uzunligi (callback_data 64 bayt cheklovi uchun)


def _b(text: str, action: str, arg: str = '') -> InlineKeyboardButton:
    return InlineKeyboardButton(text=text, callback_data=AdminCb(action=action, arg=arg).pack())


def _custom_emojis(message: Message) -> list[tuple[str, str]]:
    """[(custom emoji ID, u almashtiradigan oddiy emoji), ...] — takrorlarsiz."""
    text = message.text or message.caption or ''
    found = [(e.custom_emoji_id, e.extract_from(text))
             for e in (message.entities or message.caption_entities or [])
             if e.type == 'custom_emoji' and e.custom_emoji_id]
    if message.sticker and message.sticker.custom_emoji_id:
        found.append((message.sticker.custom_emoji_id, message.sticker.emoji or ''))
    return list(dict(found).items())


def _valid_key(key: str) -> bool:
    return key in APPEARANCE or (key.startswith(EMOJI_MAP_PREFIX)
                                 and 0 < len(key) - len(EMOJI_MAP_PREFIX) <= MAX_PLAIN_LEN)


def _name(lang: str, key: str) -> str:
    if key.startswith(EMOJI_MAP_PREFIX):
        return t(lang, 'appear_name_emap', emoji=key[len(EMOJI_MAP_PREFIX):])
    return t(lang, f'appear_name_{key}')


def _value(settings, key: str) -> str:
    if key.startswith(EMOJI_MAP_PREFIX):
        return settings.emoji_map.get(key[len(EMOJI_MAP_PREFIX):], '')
    return appearance_value(settings, key)


# ------------------------------------------------------------------ bo‘lim
async def _overview(event, lang: str, settings):
    keys = list(APPEARANCE) + [EMOJI_MAP_PREFIX + plain for plain in settings.emoji_map]
    items = []
    for key in keys:
        value = _value(settings, key)
        shown = f'<code>{value[:24]}…</code>' if len(value) > 24 else (f'<code>{value}</code>' if value else '—')
        items.append(f'{h(_name(lang, key))}: {shown}')
    rows = [[_b(t(lang, 'btn_appear_clear', name=_name(lang, key)), 'appclr', key)]
            for key in keys if _value(settings, key)]
    rows.append([_b(t(lang, 'btn_admin_panel'), 'menu')])
    await show(event, t(lang, 'appear_title', items='\n'.join(items)), InlineKeyboardMarkup(inline_keyboard=rows))


@router.message(Command('bezak', 'appearance'))
async def appearance_cmd(message: Message, lang: str, settings):
    await _overview(message, lang, settings)


@router.callback_query(AdminCb.filter(F.action == 'appear'))
async def appearance_cb(call: CallbackQuery, lang: str, settings):
    await call.answer()
    await _overview(call, lang, settings)


# ------------------------------------------------------------------ qabul qilish
# Faqat holatsiz (StateFilter(None)): admin kurs qo‘shish yoki chek yuborish jarayonida bo‘lsa, aralashmaymiz.
@router.message(StateFilter(None), F.func(lambda m: bool(_custom_emojis(m))))
async def got_custom_emoji(message: Message, state: FSMContext, lang: str):
    found = _custom_emojis(message)
    emoji_id, plain = found[0]
    await state.update_data(appear_emoji=emoji_id, appear_emoji_plain=plain)
    rows = []
    if plain and len(plain) <= MAX_PLAIN_LEN:
        rows.append([_b(t(lang, 'btn_appear_everywhere', emoji=plain), 'appset', 'emap')])
    rows += [[_b(_name(lang, key), 'appset', key)] for key in EMOJI_KEYS]
    await message.answer(t(lang, 'appear_emoji', emoji_id=emoji_id,
                           others=''.join(f'\n<code>{i}</code> {h(p)}' for i, p in found[1:])),
                         reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))


@router.message(StateFilter(None), F.sticker)
async def got_sticker(message: Message, state: FSMContext, lang: str):
    await state.update_data(appear_sticker=message.sticker.file_id)
    rows = [[_b(_name(lang, key), 'appset', key)] for key in STICKER_KEYS]
    await message.answer(t(lang, 'appear_sticker', file_id=message.sticker.file_id),
                         reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))


# ------------------------------------------------------------------ saqlash / o‘chirish
@router.callback_query(AdminCb.filter(F.action == 'appset'))
async def set_appearance(call: CallbackQuery, callback_data: AdminCb, state: FSMContext, db: Database, lang: str,
                         settings):
    data = await state.get_data()
    key = callback_data.arg
    if key == 'emap':
        # “Hamma joyda”: oddiy emoji har bir bot xabarida custom emoji bilan almashadi.
        key = EMOJI_MAP_PREFIX + (data.get('appear_emoji_plain') or '')
    if not _valid_key(key):
        await call.answer(t(lang, 'appear_expired' if callback_data.arg == 'emap' else 'outdated_button'),
                          show_alert=True)
        return
    value = data.get('appear_sticker' if key in STICKER_KEYS else 'appear_emoji')
    if not value:
        await call.answer(t(lang, 'appear_expired'), show_alert=True)
        return
    await db.set_app_setting(call.from_user.id, key, value)
    apply_appearance(settings, {key: value})
    name = _name(lang, key)
    await call.answer(t(lang, 'appear_saved', name=name))
    with contextlib.suppress(TelegramAPIError):
        await call.message.edit_reply_markup(reply_markup=None)
    if key in STICKER_KEYS:
        with contextlib.suppress(TelegramAPIError):
            await call.message.answer_sticker(value)
    if key.startswith(EMOJI_MAP_PREFIX):
        preview = key[len(EMOJI_MAP_PREFIX):]          # middleware o‘zi custom emoji’ga aylantiradi
    elif key in EMOJI_KEYS:
        preview = f'<tg-emoji emoji-id="{value}">⭐</tg-emoji>'
    else:
        preview = '✅'
    # Custom emoji ishlamasa (Telegram ruxsat bermasa), EmojiFallbackMiddleware oddiy emojiga o‘tkazadi.
    await call.message.answer(t(lang, 'appear_saved_preview', name=h(name), preview=preview),
                              reply_markup=InlineKeyboardMarkup(inline_keyboard=[[_b(t(lang, 'btn_admin_appearance'),
                                                                                     'appear')]]))


@router.callback_query(AdminCb.filter(F.action == 'appclr'))
async def clear_appearance(call: CallbackQuery, callback_data: AdminCb, db: Database, lang: str, settings):
    key = callback_data.arg
    if not _valid_key(key):
        await call.answer(t(lang, 'outdated_button'), show_alert=True)
        return
    await db.set_app_setting(call.from_user.id, key, '')
    apply_appearance(settings, {key: ''})
    await call.answer(t(lang, 'appear_cleared', name=_name(lang, key)))
    await _overview(call, lang, settings)
