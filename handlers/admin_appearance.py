"""Bezak: admin botga stiker yoki custom emoji yuboradi — bot ID’ni o‘zi aniqlaydi va qayerga qo‘yishni so‘raydi.

`.env`ni tahrirlash va botni qayta ishga tushirish shart emas: qiymat bazaga yoziladi va darhol qo‘llanadi.
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
from services.ui import APPEARANCE, apply_appearance, appearance_value

log = logging.getLogger(__name__)
router = Router(name='admin_appearance')
router.message.filter(IsAdmin())
router.callback_query.filter(IsAdmin())

STICKER_KEYS = ('sticker_welcome', 'sticker_success')
EMOJI_KEYS = ('emoji_course', 'emoji_payment', 'emoji_success')


def _b(text: str, action: str, arg: str = '') -> InlineKeyboardButton:
    return InlineKeyboardButton(text=text, callback_data=AdminCb(action=action, arg=arg).pack())


def _custom_emoji_ids(message: Message) -> list[str]:
    ids = [e.custom_emoji_id for e in (message.entities or []) if e.type == 'custom_emoji' and e.custom_emoji_id]
    if message.sticker and message.sticker.custom_emoji_id:
        ids.append(message.sticker.custom_emoji_id)
    return list(dict.fromkeys(ids))


# ------------------------------------------------------------------ bo‘lim
async def _overview(event, lang: str, settings):
    items = []
    for key in APPEARANCE:
        value = appearance_value(settings, key)
        shown = f'<code>{value[:24]}…</code>' if len(value) > 24 else (f'<code>{value}</code>' if value else '—')
        items.append(f'{t(lang, "appear_name_" + key)}: {shown}')
    rows = [[_b(t(lang, 'btn_appear_clear', name=t(lang, 'appear_name_' + key)), 'appclr', key)]
            for key in APPEARANCE if appearance_value(settings, key)]
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
@router.message(StateFilter(None), F.func(lambda m: bool(_custom_emoji_ids(m))))
async def got_custom_emoji(message: Message, state: FSMContext, lang: str):
    ids = _custom_emoji_ids(message)
    await state.update_data(appear_emoji=ids[0])
    rows = [[_b(t(lang, 'appear_name_' + key), 'appset', key)] for key in EMOJI_KEYS]
    await message.answer(t(lang, 'appear_emoji', emoji_id=ids[0],
                           others=''.join(f'\n<code>{i}</code>' for i in ids[1:])),
                         reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))


@router.message(StateFilter(None), F.sticker)
async def got_sticker(message: Message, state: FSMContext, lang: str):
    await state.update_data(appear_sticker=message.sticker.file_id)
    rows = [[_b(t(lang, 'appear_name_' + key), 'appset', key)] for key in STICKER_KEYS]
    await message.answer(t(lang, 'appear_sticker', file_id=message.sticker.file_id),
                         reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))


# ------------------------------------------------------------------ saqlash / o‘chirish
@router.callback_query(AdminCb.filter(F.action == 'appset'))
async def set_appearance(call: CallbackQuery, callback_data: AdminCb, state: FSMContext, db: Database, lang: str,
                         settings):
    key = callback_data.arg
    if key not in APPEARANCE:
        await call.answer(t(lang, 'outdated_button'), show_alert=True)
        return
    value = (await state.get_data()).get('appear_sticker' if key in STICKER_KEYS else 'appear_emoji')
    if not value:
        await call.answer(t(lang, 'appear_expired'), show_alert=True)
        return
    await db.set_app_setting(call.from_user.id, key, value)
    apply_appearance(settings, {key: value})
    name = t(lang, 'appear_name_' + key)
    await call.answer(t(lang, 'appear_saved', name=name))
    with contextlib.suppress(TelegramAPIError):
        await call.message.edit_reply_markup(reply_markup=None)
    if key in STICKER_KEYS:
        with contextlib.suppress(TelegramAPIError):
            await call.message.answer_sticker(value)
    # Custom emoji ishlamasa (Telegram ruxsat bermasa), EmojiFallbackMiddleware oddiy emojiga o‘tkazadi.
    await call.message.answer(t(lang, 'appear_saved_preview', name=name,
                                preview=f'<tg-emoji emoji-id="{value}">⭐</tg-emoji>' if key in EMOJI_KEYS else '✅'),
                              reply_markup=InlineKeyboardMarkup(inline_keyboard=[[_b(t(lang, 'btn_admin_appearance'),
                                                                                     'appear')]]))


@router.callback_query(AdminCb.filter(F.action == 'appclr'))
async def clear_appearance(call: CallbackQuery, callback_data: AdminCb, db: Database, lang: str, settings):
    key = callback_data.arg
    if key not in APPEARANCE:
        await call.answer(t(lang, 'outdated_button'), show_alert=True)
        return
    await db.set_app_setting(call.from_user.id, key, '')
    apply_appearance(settings, {key: ''})
    await call.answer(t(lang, 'appear_cleared', name=t(lang, 'appear_name_' + key)))
    await _overview(call, lang, settings)
