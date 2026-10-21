"""Kurs taqdimoti: karta matni, HTML xavfsizligi, custom emoji zaxirasi, tarjima zaxirasi."""
import unittest
from types import SimpleNamespace

from aiogram.exceptions import TelegramBadRequest
from aiogram.methods import SendMessage
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from handlers.admin_users import _cell
from services.ui import (EmojiFallbackMiddleware, course_description, course_name, emoji, map_emoji_text,
                         missing_translations, money)


class PresentationTests(unittest.TestCase):
    def test_money(self):
        self.assertEqual(money(1500000, 'uz'), '1 500 000 so‘m')
        self.assertEqual(money(990, 'ru'), '990 сум')

    def test_translation_fallback(self):
        course = {'id': 1, 'name_uz': 'Python', 'name_ru': '', 'description_uz': '', 'description_ru': 'Описание'}
        self.assertEqual(course_name(course, 'ru'), 'Python')
        self.assertEqual(course_description(course, 'uz'), 'Описание')
        self.assertEqual(missing_translations(course), ['description_uz', 'name_ru'])

    def test_custom_emoji_optional(self):
        self.assertEqual(emoji(SimpleNamespace(custom_emoji={}), 'course', '📚'), '📚')
        self.assertEqual(emoji(SimpleNamespace(custom_emoji={'course': '123456'}), 'course', '📚'),
                         '<tg-emoji emoji-id="123456">📚</tg-emoji>')

    def test_csv_formula_injection_guard(self):
        self.assertEqual(_cell('=HYPERLINK("x")'), '\'=HYPERLINK("x")')
        self.assertEqual(_cell('Ali'), 'Ali')
        self.assertEqual(_cell(None), '')


class EmojiFallbackTests(unittest.IsolatedAsyncioTestCase):
    async def test_invalid_custom_emoji_is_retried_as_plain(self):
        calls = []

        async def make_request(bot, method):
            calls.append(method)
            if len(calls) == 1:
                raise TelegramBadRequest(method=method, message='Bad Request: invalid custom emoji identifier')
            return 'ok'

        markup = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text='Pay', callback_data='x',
                                                                             icon_custom_emoji_id='111')]])
        method = SendMessage(chat_id=1, text='<tg-emoji emoji-id="111">📚</tg-emoji> Kurs', reply_markup=markup)
        self.assertEqual(await EmojiFallbackMiddleware()(make_request, None, method), 'ok')
        retried = calls[1]
        self.assertEqual(retried.text, '📚 Kurs')
        self.assertIsNone(retried.reply_markup.inline_keyboard[0][0].icon_custom_emoji_id)

    async def test_emoji_map_replaces_text_and_button_icons(self):
        sent = []

        async def make_request(bot, method):
            sent.append(method)
            return 'ok'

        emap = {'👇': '5470177992950946662', '🏠': '5877506824378257176'}
        markup = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text='🏠 Bosh menyu', callback_data='m')]])
        method = SendMessage(chat_id=1, text='🏠 <b>Menyu</b> 👇 <tg-emoji emoji-id="1">👇</tg-emoji>',
                             reply_markup=markup)
        await EmojiFallbackMiddleware(emap)(make_request, None, method)
        self.assertEqual(sent[0].text, '<tg-emoji emoji-id="5877506824378257176">🏠</tg-emoji> <b>Menyu</b> '
                                       '<tg-emoji emoji-id="5470177992950946662">👇</tg-emoji> '
                                       '<tg-emoji emoji-id="1">👇</tg-emoji>')         # mavjud teg o‘zgarmadi
        button = sent[0].reply_markup.inline_keyboard[0][0]
        self.assertEqual((button.text, button.icon_custom_emoji_id), ('Bosh menyu', '5877506824378257176'))

    async def test_emoji_map_rejected_falls_back_to_original(self):
        sent = []

        async def make_request(bot, method):
            sent.append(method)
            if '<tg-emoji' in method.text or method.reply_markup.inline_keyboard[0][0].icon_custom_emoji_id:
                raise TelegramBadRequest(method=method, message='Bad Request: custom emoji not allowed')
            return 'ok'

        markup = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text='🏠 Bosh menyu', callback_data='m')]])
        method = SendMessage(chat_id=1, text='Tanlang 👇', reply_markup=markup)
        self.assertEqual(await EmojiFallbackMiddleware({'👇': '5', '🏠': '6'})(make_request, None, method), 'ok')
        self.assertEqual(sent[-1].text, 'Tanlang 👇')
        self.assertEqual(sent[-1].reply_markup.inline_keyboard[0][0].text, '🏠 Bosh menyu')   # emoji yo‘qolmadi

    def test_emoji_map_longest_first(self):
        self.assertEqual(map_emoji_text('❤️ ❤', {'❤': '1', '❤️': '2'}),
                         '<tg-emoji emoji-id="2">❤️</tg-emoji> <tg-emoji emoji-id="1">❤</tg-emoji>')

    async def test_other_errors_are_not_swallowed(self):
        async def make_request(bot, method):
            raise TelegramBadRequest(method=method, message='Bad Request: chat not found')

        with self.assertRaises(TelegramBadRequest):
            await EmojiFallbackMiddleware()(make_request, None, SendMessage(chat_id=1, text='plain'))


if __name__ == '__main__':
    unittest.main()
