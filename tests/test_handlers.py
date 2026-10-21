"""Handlerlarni haqiqiy aiogram Dispatcher orqali, tarmoqsiz sessiya bilan sinash."""
import unittest
from datetime import datetime, timezone

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.methods import AnswerCallbackQuery, SendDocument
from aiogram.types import CallbackQuery, Chat, Message, Update, User

import handlers
from keyboards.callbacks import AdminCb, LangCb, UserCb
from locales import TEXTS
from services.payments import PaymentService
from tests.helpers import ADMIN_ID, USER_ID, FakeTezcheck, RecordingSession, make_db, make_settings, seed


class HandlerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.db, self.tmp = await make_db()
        self.settings = make_settings(self.db.path)
        self.session = RecordingSession()
        self.bot = Bot('123456:' + 'A' * 35, session=self.session, default=DefaultBotProperties(parse_mode='HTML'))
        self.payments = PaymentService(self.db, FakeTezcheck(), self.bot, self.settings)
        self.dp = Dispatcher(storage=MemoryStorage(), settings=self.settings, db=self.db, payments=self.payments)
        handlers.setup(self.dp)
        self.cid = await seed(self.db)
        await self.db.set_language(ADMIN_ID, 'uz')
        self.update_id = 0
        self.addAsyncCleanup(self._cleanup)

    async def _cleanup(self):
        await self.payments.shutdown()
        await self.db.close()
        self.tmp.cleanup()

    def _user(self, uid):
        return User(id=uid, is_bot=False, first_name=f'U{uid}', username=f'u{uid}')

    async def send_text(self, uid: int, text: str):
        self.update_id += 1
        msg = Message(message_id=self.update_id, date=datetime.now(timezone.utc), chat=Chat(id=uid, type='private'),
                      from_user=self._user(uid), text=text)
        await self.dp.feed_update(self.bot, Update(update_id=self.update_id, message=msg))

    async def press(self, uid: int, data: str):
        self.update_id += 1
        msg = Message(message_id=1, date=datetime.now(timezone.utc), chat=Chat(id=uid, type='private'), text='old')
        call = CallbackQuery(id=str(self.update_id), from_user=self._user(uid), chat_instance='ci', data=data,
                             message=msg)
        await self.dp.feed_update(self.bot, Update(update_id=self.update_id, callback_query=call))

    def admin_title_shown(self) -> bool:
        return any('Admin panel' in t or 'Админ-панель' in t for t in self.session.texts())

    # ------------------------------------------------------------ admin ruxsati
    async def test_admin_can_open_panel(self):
        await self.send_text(ADMIN_ID, '/admin')
        self.assertTrue(self.admin_title_shown())

    async def test_non_admin_cannot_open_panel(self):
        await self.send_text(USER_ID, '/admin')
        await self.send_text(USER_ID, TEXTS['uz']['btn_admin'])
        self.assertFalse(self.admin_title_shown())

    async def test_forged_admin_callbacks_are_rejected(self):
        for action in ('menu', 'delok', 'toggle', 'export', 'blockok', 'users'):
            await self.press(USER_ID, AdminCb(action=action, id=self.cid if action != 'blockok' else ADMIN_ID).pack())
        answers = [m for m in self.session.requests if isinstance(m, AnswerCallbackQuery)]
        self.assertEqual(len(answers), 6)
        self.assertTrue(all(a.show_alert and a.text == TEXTS['uz']['no_permission'] for a in answers))
        self.assertFalse(self.admin_title_shown())
        self.assertFalse(any(isinstance(m, SendDocument) for m in self.session.requests))
        course = await self.db.get_course(self.cid)
        self.assertIsNotNone(course)                 # o‘chirilmadi
        self.assertTrue(course['is_active'])         # holati o‘zgarmadi
        self.assertFalse((await self.db.get_user(ADMIN_ID))['is_blocked'])
        self.assertEqual((await self.db.admin_logs(0))[1], 1)   # faqat seed’dagi kurs qo‘shish

    async def test_admin_can_delete_course_after_confirmation(self):
        await self.press(ADMIN_ID, AdminCb(action='del', id=self.cid).pack())
        self.assertIsNotNone(await self.db.get_course(self.cid))   # faqat tasdiq so‘raldi
        await self.press(ADMIN_ID, AdminCb(action='delok', id=self.cid).pack())
        self.assertIsNone(await self.db.get_course(self.cid))

    async def test_admin_add_course_wizard(self):
        await self.press(ADMIN_ID, AdminCb(action='add').pack())
        await self.send_text(ADMIN_ID, 'Dizayn asoslari')
        await self.send_text(ADMIN_ID, 'Figma va kompozitsiya asoslari')
        await self.press(ADMIN_ID, AdminCb(action='skip').pack())      # ruscha nom — o‘tkazildi
        await self.press(ADMIN_ID, AdminCb(action='skip').pack())      # ruscha tavsif — o‘tkazildi
        await self.send_text(ADMIN_ID, '-5')                            # noto‘g‘ri narx
        await self.send_text(ADMIN_ID, '250 000')
        await self.send_text(ADMIN_ID, '-1009876543210')                # kanal: soxta sessiyada tekshiruv o‘tadi
        await self.press(ADMIN_ID, AdminCb(action='save').pack())
        courses, total = await self.db.list_courses(0, active_only=False)
        self.assertEqual(total, 2)
        new = courses[-1]
        self.assertEqual((new['name_uz'], new['name_ru'], new['price_uzs'], new['channel_chat_id']),
                         ('Dizayn asoslari', '', 250000, -1009876543210))
        self.assertTrue(any(TEXTS['uz']['invalid_price'] in t for t in self.session.texts()))
        self.assertTrue(any('Tarjima yetishmaydi' in t for t in self.session.texts()))

    async def test_cancel_in_wizard(self):
        await self.press(ADMIN_ID, AdminCb(action='add').pack())
        await self.send_text(ADMIN_ID, 'Bekor qilinadigan kurs')
        await self.press(ADMIN_ID, AdminCb(action='cancel').pack())
        await self.send_text(ADMIN_ID, 'Bu matn kurs bo‘lmasligi kerak')
        self.assertEqual((await self.db.list_courses(0, active_only=False))[1], 1)

    # ------------------------------------------------------------ til va deep link
    async def test_first_start_asks_language_then_opens_deep_link(self):
        new_user = 7777
        await self.send_text(new_user, f'/start course_{self.cid}')
        self.assertIn('Tilni tanlang', self.session.texts()[-1])
        await self.press(new_user, LangCb(code='ru').pack())
        self.assertEqual((await self.db.get_user(new_user))['language'], 'ru')
        self.assertTrue(any('Курс Python' in t and 'Стоимость' in t for t in self.session.texts()))

    async def test_language_switch_keeps_orders(self):
        result = await self.payments.checkout(USER_ID, self.cid)
        await self.press(USER_ID, LangCb(code='ru').pack())
        self.assertEqual((await self.db.get_order(result.order['id']))['status'], 'pending')
        await self.press(USER_ID, UserCb(action='catalog').pack())
        self.assertTrue(any('Курсы' in t for t in self.session.texts()))

    async def test_unknown_deep_link(self):
        await self.send_text(USER_ID, '/start course_99999')
        self.assertTrue(any(TEXTS['uz']['course_not_found'] in t for t in self.session.texts()))

    async def test_user_cannot_check_someone_elses_order(self):
        result = await self.payments.checkout(USER_ID, self.cid)
        await self.db.touch_user(4242, 'other', 'Other')
        await self.press(4242, UserCb(action='check', id=result.order['id']).pack())
        answers = [m for m in self.session.requests if isinstance(m, AnswerCallbackQuery)]
        self.assertEqual(answers[-1].text, TEXTS['uz']['outdated_button'])

    async def test_blocked_user_is_stopped_and_activity_tracked(self):
        await self.db.set_blocked(ADMIN_ID, USER_ID, True)
        await self.send_text(USER_ID, TEXTS['uz']['btn_courses'])
        self.assertEqual(self.session.texts()[-1], TEXTS['uz']['blocked'])
        self.assertIsNotNone((await self.db.get_user(USER_ID))['last_active_at'])

    async def test_admin_users_section_and_csv_export(self):
        await self.press(ADMIN_ID, AdminCb(action='users', arg='buyers.active').pack())
        self.assertTrue(any('Foydalanuvchilar' in t for t in self.session.texts()))
        await self.press(ADMIN_ID, AdminCb(action='export').pack())
        docs = [m for m in self.session.requests if isinstance(m, SendDocument)]
        self.assertEqual(len(docs), 1)
        content = docs[0].document.data.decode('utf-8-sig')
        self.assertIn('telegram_id', content)
        self.assertIn(str(USER_ID), content)


if __name__ == '__main__':
    unittest.main()
