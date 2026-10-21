"""Xaridor klaviaturalari."""
from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from keyboards.callbacks import AdminCb, UserCb
from keyboards.common import add_pagination
from locales import LANGUAGE_BUTTON, t
from services.ui import course_name, money

CATALOG_PAGE = 8


def _btn(text: str, cb: UserCb | AdminCb | None = None, url: str | None = None, style: str | None = None,
         icon: str | None = None) -> InlineKeyboardButton:
    return InlineKeyboardButton(text=text, callback_data=cb.pack() if cb else None, url=url, style=style,
                                icon_custom_emoji_id=icon)


def support_button(lang: str, settings) -> list[InlineKeyboardButton]:
    if settings.support_username:
        return [_btn(t(lang, 'btn_support'), url=f'https://t.me/{settings.support_username}')]
    return []


def main_menu_kb(lang: str, is_admin: bool, settings) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.row(_btn(t(lang, 'btn_courses'), UserCb(action='catalog'), style='primary',
               icon=settings.custom_emoji.get('course')))
    b.row(_btn(t(lang, 'btn_my_courses'), UserCb(action='mine')), _btn(t(lang, 'btn_help'), UserCb(action='help')))
    b.row(_btn(LANGUAGE_BUTTON, UserCb(action='lang')))
    if is_admin:
        b.row(_btn(t(lang, 'btn_admin'), AdminCb(action='menu')))
    return b.as_markup()


def catalog_kb(lang: str, courses: list[dict], page: int, total: int) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    for course in courses:
        b.row(_btn(t(lang, 'course_button', name=course_name(course, lang)[:40],
                     price=money(course['price_uzs'], lang)), UserCb(action='course', id=course['id'])))
    add_pagination(b, lang, page, total, CATALOG_PAGE, lambda p: UserCb(action='catalog', page=p).pack())
    b.row(_btn(t(lang, 'btn_main_menu'), UserCb(action='menu')), _btn(LANGUAGE_BUTTON, UserCb(action='lang')))
    return b.as_markup()


def course_kb(lang: str, course_id: int, owned: bool, purchasable: bool, settings) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    if owned:
        b.row(_btn(t(lang, 'btn_get_link'), UserCb(action='link', id=course_id), style='success'))
    elif purchasable:
        b.row(_btn(t(lang, 'btn_pay'), UserCb(action='buy', id=course_id), style='success',
                   icon=settings.custom_emoji.get('payment')))
        if settings.card_number:
            b.row(_btn(t(lang, 'btn_pay_card'), UserCb(action='card', id=course_id)))
    b.row(*support_button(lang, settings))
    b.row(_btn(t(lang, 'btn_all_courses'), UserCb(action='catalog')), _btn(LANGUAGE_BUTTON, UserCb(action='lang')))
    return b.as_markup()


def payment_kb(lang: str, order: dict, settings) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.row(_btn(t(lang, 'btn_pay_now'), url=order['payment_url'], style='success',
               icon=settings.custom_emoji.get('payment')))
    b.row(_btn(t(lang, 'btn_check_payment'), UserCb(action='check', id=order['id']), style='primary'))
    if settings.card_number:
        b.row(_btn(t(lang, 'btn_pay_card'), UserCb(action='card', id=order['course_id'])))
    b.row(*support_button(lang, settings))
    b.row(_btn(t(lang, 'btn_my_courses'), UserCb(action='mine')), _btn(LANGUAGE_BUTTON, UserCb(action='lang')))
    return b.as_markup()


def card_payment_kb(lang: str, course_id: int, settings) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.row(*support_button(lang, settings))
    b.row(_btn(t(lang, 'btn_back'), UserCb(action='course', id=course_id)),
          _btn(t(lang, 'btn_main_menu'), UserCb(action='menu')))
    return b.as_markup()


def my_courses_kb(lang: str, purchases: list[dict], orders: list[dict], settings) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    for p in purchases:
        b.row(_btn(f'🔗 {course_name(p, lang)[:45]}', UserCb(action='link', id=p['course_id'])))
    for o in orders:
        b.row(_btn(f'🔄 #{o["id"]} · {o["course_title"][:35]}', UserCb(action='check', id=o['id'])))
    b.row(*support_button(lang, settings))
    b.row(_btn(t(lang, 'btn_courses'), UserCb(action='catalog')), _btn(LANGUAGE_BUTTON, UserCb(action='lang')))
    return b.as_markup()


def back_to_menu_kb(lang: str, settings) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.row(*support_button(lang, settings))
    b.row(_btn(t(lang, 'btn_courses'), UserCb(action='catalog')), _btn(t(lang, 'btn_main_menu'), UserCb(action='menu')))
    return b.as_markup()
