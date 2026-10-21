"""Karta + chek orqali to‘lov va eskirgan Tezcheck havolasini yangilash."""
import asyncio
import dataclasses
import unittest
from datetime import datetime, timezone

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.methods import SendPhoto
from aiogram.types import Chat, Message, PhotoSize, Update, User

import handlers
from keyboards.callbacks import AdminCb, UserCb
from locales import TEXTS
from services.payments import PaymentService
from tests.helpers import (ADMIN_ID, USER_ID, FakeBot, FakeTezcheck, RecordingSession, make_db, make_settings,
                           seed)

CARD = '8600 1234 5678 9012'


class ReceiptServiceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.db, self.tmp = await make_db()
        self.addAsyncCleanup(self._cleanup)
        self.settings = make_settings(self.db.path, card_number=CARD)
        self.api, self.bot = FakeTezcheck(), FakeBot()
        self.payments = PaymentService(self.db, self.api, self.bot, self.settings)
        self.cid = await seed(self.db)
        _, self.order = await self.db.get_or_create_open_order(USER_ID, self.cid)

    async def _cleanup(self):
        await self.payments.shutdown()
        await self.db.close()
        self.tmp.cleanup()

    async def _submit(self) -> int:
        self.assertEqual(await self.payments.submit_receipt(self.order['id'], USER_ID, 'file1', 'photo'), 'sent')
        return (await self.db.one('SELECT id FROM receipts ORDER BY id DESC LIMIT 1'))['id']

    async def test_receipt_goes_to_admin_with_buttons(self):
        rid = await self._submit()
        chat_id, caption, markup = self.bot.messages[-1]
        self.assertEqual(chat_id, ADMIN_ID)
        self.assertIn(f'#{self.order["id"]}', caption)
        data = [b.callback_data for row in markup.inline_keyboard for b in row]
        self.assertEqual(data, [AdminCb(action='rcok', id=rid).pack(), AdminCb(action='rcno', id=rid).pack()])

    async def test_second_receipt_while_pending_is_refused(self):
        await self._submit()
        self.assertEqual(await self.payments.submit_receipt(self.order['id'], USER_ID, 'file2', 'photo'), 'busy')

    async def test_foreign_order_is_refused(self):
        self.assertEqual(await self.payments.submit_receipt(self.order['id'], 4242, 'f', 'photo'), 'not_found')

    async def test_approve_creates_purchase_and_sends_link_once(self):
        rid = await self._submit()
        results = await asyncio.gather(*(self.payments.decide_receipt(rid, ADMIN_ID, True) for _ in range(3)))
        self.assertEqual(sorted(r[0] for r in results), ['already', 'already', 'approved'])
        order = await self.db.get_order(self.order['id'])
        self.assertEqual((order['status'], order['payment_environment']), ('paid', 'card'))
        self.assertEqual(len(self.bot.links_sent_to(USER_ID)), 1)
        self.assertEqual(await self.payments.submit_receipt(self.order['id'], USER_ID, 'f', 'photo'), 'paid')

    async def test_reject_notifies_user_and_allows_resubmit(self):
        rid = await self._submit()
        status, _ = await self.payments.decide_receipt(rid, ADMIN_ID, False)
        self.assertEqual(status, 'rejected')
        self.assertIsNone(await self.db.purchase_for(USER_ID, self.cid))
        self.assertIn(f'#{self.order["id"]}', self.bot.messages[-1][1])
        self.assertEqual(self.bot.messages[-1][0], USER_ID)
        self.assertEqual(await self.payments.submit_receipt(self.order['id'], USER_ID, 'f2', 'photo'), 'sent')

    async def test_approve_after_tezcheck_payment_does_not_duplicate(self):
        result = await self.payments.checkout(USER_ID, self.cid)
        rid = await self._submit()
        self.api.pay(result.order['tezcheck_bill_id'])
        self.assertEqual(await self.payments.check_order(result.order['id']), 'paid')
        status, _ = await self.payments.decide_receipt(rid, ADMIN_ID, True)
        self.assertEqual(status, 'approved')
        self.assertEqual(len(self.bot.links_sent_to(USER_ID)), 1)


class DeadBillTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.db, self.tmp = await make_db()
        self.addAsyncCleanup(self._cleanup)
        self.api, self.bot = FakeTezcheck(), FakeBot()
        self.payments = PaymentService(self.db, self.api, self.bot, make_settings(self.db.path))
        self.cid = await seed(self.db)

    async def _cleanup(self):
        await self.payments.shutdown()
        await self.db.close()
        self.tmp.cleanup()

    async def test_dead_bill_is_replaced_with_fresh_one(self):
        first = await self.payments.checkout(USER_ID, self.cid)
        self.api.bills[first.order['tezcheck_bill_id']]['state'] = 'exhausted'
        second = await self.payments.checkout(USER_ID, self.cid)
        self.assertEqual(second.kind, 'ready')
        self.assertNotEqual(first.order['payment_url'], second.order['payment_url'])
        self.assertEqual((await self.db.get_order(first.order['id']))['status'], 'expired')

    async def test_time_expired_active_bill_is_replaced(self):
        first = await self.payments.checkout(USER_ID, self.cid)
        real_get = self.api.get_bill

        async def expired_get(bill_id):
            bill = await real_get(bill_id)
            if bill_id == first.order['tezcheck_bill_id']:
                return dataclasses.replace(bill, unavailability_reason='expired')   # state hali ham 'active'
            return bill
        self.api.get_bill = expired_get
        second = await self.payments.checkout(USER_ID, self.cid)
        self.assertNotEqual(first.order['payment_url'], second.order['payment_url'])

    async def test_live_bill_is_reused(self):
        first = await self.payments.checkout(USER_ID, self.cid)
        second = await self.payments.checkout(USER_ID, self.cid)
        self.assertEqual(first.order['payment_url'], second.order['payment_url'])
        self.assertEqual(len(self.api.bills), 1)

    async def test_paid_bill_returns_purchase(self):
        first = await self.payments.checkout(USER_ID, self.cid)
        self.api.pay(first.order['tezcheck_bill_id'])
        self.assertEqual((await self.payments.checkout(USER_ID, self.cid)).kind, 'purchased')


class ReceiptHandlerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.db, self.tmp = await make_db()
        self.settings = make_settings(self.db.path, card_number=CARD, card_holder='Ali Valiyev')
        self.session = RecordingSession()
        self.bot = Bot('123456:' + 'A' * 35, session=self.session, default=DefaultBotProperties(parse_mode='HTML'))
        self.payments = PaymentService(self.db, FakeTezcheck(), self.bot, self.settings)
        self.dp = Dispatcher(storage=MemoryStorage(), settings=self.settings, db=self.db, payments=self.payments)
        handlers.setup(self.dp)
        self.cid = await seed(self.db)
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

    async def _feed_message(self, uid, **kw):
        await self.dp.feed_update(self.bot, Update(update_id=self.n + 1, message=self._msg(uid, **kw)))

    async def _press(self, uid, data, message=None):
        from aiogram.types import CallbackQuery
        self.n += 1
        call = CallbackQuery(id=str(self.n), from_user=User(id=uid, is_bot=False, first_name='U'), chat_instance='ci',
                             data=data, message=message or self._msg(uid, text='old'))
        await self.dp.feed_update(self.bot, Update(update_id=self.n, callback_query=call))

    async def test_full_card_flow(self):
        await self._press(USER_ID, UserCb(action='course', id=self.cid).pack())
        self.assertIn(TEXTS['uz']['btn_pay_card'], str(self.session.requests[-1].reply_markup))
        await self._press(USER_ID, UserCb(action='card', id=self.cid).pack())
        self.assertIn(CARD, self.session.texts()[-1])
        self.assertIn('Ali Valiyev', self.session.texts()[-1])

        await self._feed_message(USER_ID, text='salom')                 # rasm emas
        self.assertEqual(self.session.texts()[-1], TEXTS['uz']['receipt_wrong_type'])
        await self._feed_message(USER_ID, photo=[PhotoSize(file_id='small', file_unique_id='s', width=90, height=90),
                                                 PhotoSize(file_id='big', file_unique_id='b', width=900, height=900)])
        self.assertEqual(self.session.texts()[-1], TEXTS['uz']['receipt_received'])
        photos = [m for m in self.session.requests if isinstance(m, SendPhoto)]
        self.assertEqual((photos[-1].chat_id, photos[-1].photo), (ADMIN_ID, 'big'))

        rid = (await self.db.one('SELECT id FROM receipts'))['id']
        admin_msg = self._msg(ADMIN_ID, photo=[PhotoSize(file_id='big', file_unique_id='b', width=9, height=9)],
                              caption='Yangi chek')
        await self._press(ADMIN_ID, AdminCb(action='rcok', id=rid).pack(), message=admin_msg)
        self.assertIsNotNone(await self.db.purchase_for(USER_ID, self.cid))
        self.assertTrue(any('t.me/+invite' in str(getattr(m, 'reply_markup', '')) for m in self.session.requests))

    async def test_non_admin_cannot_approve(self):
        _, order = await self.db.get_or_create_open_order(USER_ID, self.cid)
        rid = await self.db.add_receipt(order['id'], USER_ID, 'f', 'photo')
        await self._press(USER_ID, AdminCb(action='rcok', id=rid).pack())
        self.assertEqual((await self.db.get_receipt(rid))['status'], 'pending')
        self.assertIsNone(await self.db.purchase_for(USER_ID, self.cid))

    async def test_card_button_hidden_without_card_number(self):
        self.settings = make_settings(self.db.path)
        self.dp['settings'] = self.settings
        await self._press(USER_ID, UserCb(action='course', id=self.cid).pack())
        self.assertNotIn(TEXTS['uz']['btn_pay_card'], str(self.session.requests[-1].reply_markup))


if __name__ == '__main__':
    unittest.main()
