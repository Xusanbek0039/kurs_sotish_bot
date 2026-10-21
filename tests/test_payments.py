import asyncio
import unittest

from database import ts
from services.payments import PaymentService
from services.tezcheck import TezcheckError
from tests.helpers import ADMIN_ID, CHANNEL_ID, USER_ID, FakeBot, FakeTezcheck, make_db, make_settings, seed


class PaymentFlowTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.db, self.tmp = await make_db()
        self.addAsyncCleanup(self._cleanup)
        self.settings = make_settings(self.db.path)
        self.api, self.bot = FakeTezcheck(), FakeBot()
        self.payments = PaymentService(self.db, self.api, self.bot, self.settings)
        self.cid = await seed(self.db)

    async def _cleanup(self):
        await self.payments.shutdown()
        await self.db.close()
        self.tmp.cleanup()

    async def _pending_order(self):
        result = await self.payments.checkout(USER_ID, self.cid)
        self.assertEqual(result.kind, 'ready')
        return result.order

    # --------------------------------------------------------- buyurtma idempotentligi
    async def test_checkout_is_idempotent(self):
        results = await asyncio.gather(*(self.payments.checkout(USER_ID, self.cid) for _ in range(5)))
        self.assertEqual({r.kind for r in results}, {'ready'})
        self.assertEqual(len({r.order['id'] for r in results}), 1)
        self.assertEqual(len({r.order['payment_url'] for r in results}), 1)
        self.assertEqual(len(self.api.created), 1)                 # Tezcheck’ga bitta so‘rov
        sent = self.api.created[0]
        self.assertEqual(sent['amount_minor'], 150000 * 100)      # so‘m → tiyin
        self.assertEqual(sent['ref'], results[0].order['external_reference'])
        self.assertTrue(sent['key'])

    async def test_retry_after_api_failure_reuses_same_idempotency_key(self):
        self.api.fail_create = TezcheckError('network_error', None, True)
        failed = await self.payments.checkout(USER_ID, self.cid)
        self.assertEqual(failed.kind, 'error')
        self.api.fail_create = None
        ok = await self.payments.checkout(USER_ID, self.cid)
        self.assertEqual(ok.kind, 'ready')
        self.assertEqual(failed.order['id'], ok.order['id'])
        self.assertEqual(self.api.created[0]['key'], self.api.created[1]['key'])
        self.assertEqual(len(self.api.bills), 1)

    async def test_inactive_course_cannot_be_paid(self):
        await self.db.toggle_course(ADMIN_ID, self.cid)
        self.assertEqual((await self.payments.checkout(USER_ID, self.cid)).kind, 'unavailable')
        self.assertEqual(self.api.created, [])

    # --------------------------------------------------------- tasdiqlanmaguncha havola yo‘q
    async def test_no_invite_without_confirmed_payment(self):
        order = await self._pending_order()
        self.assertEqual(await self.payments.check_order(order['id']), 'pending')
        self.assertEqual(self.bot.invites, [])
        self.assertEqual(self.bot.links_sent_to(USER_ID), [])
        self.assertIsNone(await self.db.purchase_for(USER_ID, self.cid))

    async def test_deliver_refuses_unpaid_order_even_if_purchase_row_exists(self):
        order = await self._pending_order()
        # Himoya qatlamini sinash: qo‘lda (noto‘g‘ri) xarid yozuvi yaratilgan bo‘lsa ham havola berilmaydi.
        cur = await self.db.write("INSERT INTO purchases(user_id, course_id, order_id, status, created_at, updated_at) "
                                  "VALUES (?,?,?,'pending_invite',?,?)", (USER_ID, self.cid, order['id'], ts(), ts()))
        self.assertEqual(await self.payments.deliver(cur.lastrowid), 'not_paid')
        self.assertEqual(self.bot.invites, [])

    async def test_paid_order_delivers_exactly_one_invite(self):
        order = await self._pending_order()
        self.api.pay(order['tezcheck_bill_id'])
        self.assertEqual(await self.payments.check_order(order['id']), 'paid')
        self.assertEqual(await self.payments.check_order(order['id']), 'paid')
        await self.payments.poll_once()
        self.assertEqual(len(self.bot.invites), 1)
        invite = self.bot.invites[0]
        self.assertEqual(invite['chat_id'], CHANNEL_ID)
        self.assertEqual(invite['member_limit'], 1)
        self.assertIsNotNone(invite['expire_date'])
        self.assertEqual(self.bot.links_sent_to(USER_ID), [invite['link']])
        purchase = await self.db.purchase_for(USER_ID, self.cid)
        self.assertEqual(purchase['status'], 'sent')
        self.assertIsNotNone(purchase['invite_sent_at'])
        self.assertEqual((await self.db.get_order(order['id']))['status'], 'paid')

    async def test_test_provider_payment_is_not_accepted(self):
        order = await self._pending_order()
        self.api.pay(order['tezcheck_bill_id'], environment='test')
        self.assertEqual(await self.payments.check_order(order['id']), 'test')
        self.assertEqual((await self.db.get_order(order['id']))['status'], 'pending')
        self.assertEqual(self.bot.invites, [])

    async def test_amount_mismatch_goes_to_review(self):
        order = await self._pending_order()
        self.api.pay(order['tezcheck_bill_id'], amount=100)
        self.assertEqual(await self.payments.check_order(order['id']), 'review')
        self.assertEqual((await self.db.get_order(order['id']))['status'], 'review')
        self.assertEqual(self.bot.invites, [])
        self.assertTrue(any(chat == ADMIN_ID for chat, _, _ in self.bot.messages))

    async def test_expired_bill(self):
        order = await self._pending_order()
        self.api.bills[order['tezcheck_bill_id']]['state'] = 'expired'
        self.assertEqual(await self.payments.check_order(order['id'], notify=True), 'expired')
        self.assertEqual((await self.db.get_order(order['id']))['status'], 'expired')

    async def test_api_error_keeps_order_pending(self):
        order = await self._pending_order()
        self.api.fail_get = TezcheckError('invalid_response:json')
        self.assertEqual(await self.payments.check_order(order['id']), 'error')
        self.assertEqual((await self.db.get_order(order['id']))['status'], 'pending')

    # --------------------------------------------------------- yetkazish xatolari
    async def test_invite_failure_alerts_and_admin_can_resend(self):
        order = await self._pending_order()
        self.api.pay(order['tezcheck_bill_id'])
        self.bot.fail_invite = True
        await self.payments.check_order(order['id'])
        purchase = await self.db.purchase_for(USER_ID, self.cid)
        self.assertEqual(purchase['status'], 'invite_failed')
        self.assertTrue(any(chat == ADMIN_ID for chat, _, _ in self.bot.messages))      # admin ogohlantirildi
        self.assertTrue(any(chat == USER_ID for chat, _, _ in self.bot.messages))       # xaridorga yordam xabari
        await self.payments.poll_once()       # kanal xatosi avtomatik takrorlanmaydi
        self.assertEqual(self.bot.invites, [])
        self.bot.fail_invite = False
        self.assertEqual(await self.payments.deliver(purchase['id'], actor='admin', new_link=True), 'sent')
        self.assertEqual(len(self.bot.invites), 1)

    async def test_blocked_user_gets_link_later(self):
        order = await self._pending_order()
        self.api.pay(order['tezcheck_bill_id'])
        self.bot.blocked_users.add(USER_ID)
        await self.payments.check_order(order['id'])
        purchase = await self.db.purchase_for(USER_ID, self.cid)
        self.assertEqual(purchase['status'], 'send_failed')
        self.assertIsNotNone(purchase['invite_link'])          # havola saqlangan
        self.bot.blocked_users.clear()
        await self.payments.deliver_pending_for_user(USER_ID)  # foydalanuvchi /start bosganda
        self.assertEqual(len(self.bot.invites), 1)              # yangi havola yaratilmadi
        self.assertEqual(self.bot.links_sent_to(USER_ID), [purchase['invite_link']])
        self.assertEqual((await self.db.purchase_for(USER_ID, self.cid))['status'], 'sent')

    async def test_user_link_request_limits(self):
        order = await self._pending_order()
        self.api.pay(order['tezcheck_bill_id'])
        await self.payments.check_order(order['id'])
        purchase = await self.db.purchase_for(USER_ID, self.cid)
        self.bot.members.add(USER_ID)
        self.assertEqual(await self.payments.deliver(purchase['id'], actor='user'), 'member')
        self.bot.members.clear()
        self.assertEqual(await self.payments.deliver(purchase['id'], actor='user'), 'sent')   # o‘sha havola
        self.assertEqual(len(self.bot.invites), 1)
        await self.db.write("UPDATE purchases SET invite_expires_at = '2000-01-01 00:00:00', invite_count = 3")
        self.assertEqual(await self.payments.deliver(purchase['id'], actor='user'), 'limit')
        self.assertEqual(len(self.bot.invites), 1)


if __name__ == '__main__':
    unittest.main()
