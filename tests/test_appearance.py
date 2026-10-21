"""Bezak: admin botga stiker / custom emoji yuborib, ularni ish paytida almashtiradi."""
import unittest
from datetime import datetime, timezone

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import CallbackQuery, Chat, Message, MessageEntity, Sticker, Update, User

import handlers
from keyboards.callbacks import AdminCb
from services.payments import PaymentService
from services.ui import apply_appearance
from tests.helpers import ADMIN_ID, USER_ID, FakeTezcheck, RecordingSession, make_db, make_settings, seed


class AppearanceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.db, self.tmp = await make_db()
        self.settings = make_settings(self.db.path)
        self.session = RecordingSession()
        self.bot = Bot('123456:' + 'A' * 35, session=self.session, default=DefaultBotProperties(parse_mode='HTML'))
        self.payments = PaymentService(self.db, FakeTezcheck(), self.bot, self.settings)
        self.dp = Dispatcher(storage=MemoryStorage(), settings=self.settings, db=self.db, payments=self.payments)
        handlers.setup(self.dp)
        await seed(self.db)
        await self.db.set_language(ADMIN_ID, 'uz')
        self.n = 0
        self.addAsyncCleanup(self._cleanup)

    async def _cleanup(self):
        for router in (handlers.admin.router, handlers.admin_users.router, handlers.admin_appearance.router,
                       handlers.user.router, handlers.fallback):
            router._parent_router = None
        await self.payments.shutdown()
        await self.db.close()
        self.tmp.cleanup()

    def _msg(self, uid, **kw):
        self.n += 1
        return Message(message_id=self.n, date=datetime.now(timezone.utc), chat=Chat(id=uid, type='private'),
                       from_user=User(id=uid, is_bot=False, first_name='U'), **kw)

    async def _send(self, uid, **kw):
        await self.dp.feed_update(self.bot, Update(update_id=self.n + 1, message=self._msg(uid, **kw)))

    async def _press(self, uid, data):
        self.n += 1
        call = CallbackQuery(id=str(self.n), from_user=User(id=uid, is_bot=False, first_name='U'), chat_instance='ci',
                             data=data, message=self._msg(uid, text='old'))
        await self.dp.feed_update(self.bot, Update(update_id=self.n, callback_query=call))

    @staticmethod
    def _sticker(file_id='STK_FILE_1', custom_emoji_id=None):
        return Sticker(file_id=file_id, file_unique_id='u', type='custom_emoji' if custom_emoji_id else 'regular',
                       width=512, height=512, is_animated=False, is_video=False, custom_emoji_id=custom_emoji_id)

    async def test_admin_sets_welcome_sticker_by_sending_it(self):
        await self._send(ADMIN_ID, sticker=self._sticker())
        self.assertIn('STK_FILE_1', self.session.texts()[-1])
        await self._press(ADMIN_ID, AdminCb(action='appset', arg='sticker_welcome').pack())
        self.assertEqual(self.settings.stickers['welcome'], 'STK_FILE_1')       # darhol ishlaydi
        self.assertEqual((await self.db.app_settings())['sticker_welcome'], 'STK_FILE_1')   # qayta ishga tushsa ham

    async def test_admin_sets_custom_emoji_from_text(self):
        await self._send(ADMIN_ID, text='⭐', entities=[MessageEntity(type='custom_emoji', offset=0, length=1,
                                                                     custom_emoji_id='5368324170671202286')])
        self.assertIn('5368324170671202286', self.session.texts()[-1])
        await self._press(ADMIN_ID, AdminCb(action='appset', arg='emoji_course').pack())
        self.assertEqual(self.settings.custom_emoji['course'], '5368324170671202286')

    async def test_clear_removes_value(self):
        await self._send(ADMIN_ID, sticker=self._sticker())
        await self._press(ADMIN_ID, AdminCb(action='appset', arg='sticker_success').pack())
        await self._press(ADMIN_ID, AdminCb(action='appclr', arg='sticker_success').pack())
        self.assertNotIn('success', self.settings.stickers)
        fresh = make_settings(self.db.path, stickers={'success': 'FROM_ENV'})
        apply_appearance(fresh, await self.db.app_settings())
        self.assertNotIn('success', fresh.stickers)                             # .env qiymatini ham bekor qiladi

    async def test_non_admin_sticker_is_ignored(self):
        await self._send(USER_ID, sticker=self._sticker())
        await self._press(USER_ID, AdminCb(action='appset', arg='sticker_welcome').pack())
        self.assertNotIn('welcome', self.settings.stickers)
        self.assertEqual(await self.db.app_settings(), {})

    async def test_overview_lists_values(self):
        apply_appearance(self.settings, {'emoji_payment': '123456789'})
        await self._send(ADMIN_ID, text='/bezak')
        self.assertIn('123456789', self.session.texts()[-1])


if __name__ == '__main__':
    unittest.main()
