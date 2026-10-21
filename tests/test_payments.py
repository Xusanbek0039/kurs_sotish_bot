import asyncio
import json
import time
import unittest

import httpx

from database import ts
from services.payments import PaymentService
from services.tezcheck import TezcheckError
from services.webhook import create_app
from tests.helpers import (ADMIN_ID, CHANNEL_ID, USER_ID, FakeBot, FakeTezcheck, make_db, make_settings, seed,
                           sign_webhook, webhook_body)


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


class WebhookTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.db, self.tmp = await make_db()
        self.settings = make_settings(self.db.path)
        self.api, self.bot = FakeTezcheck(), FakeBot()
        self.payments = PaymentService(self.db, self.api, self.bot, self.settings)
        self.cid = await seed(self.db)
        app = create_app(self.settings, self.db, self.payments)
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test')
        self.addAsyncCleanup(self._cleanup)
        self.order = (await self.payments.checkout(USER_ID, self.cid)).order
        self.bill_id = self.order['tezcheck_bill_id']

    async def _cleanup(self):
        await self.client.aclose()
        await self.payments.shutdown()
        await self.db.close()
        self.tmp.cleanup()

    async def post(self, body: bytes, headers: dict):
        response = await self.client.post(self.settings.webhook_path, content=body, headers=headers)
        await self.payments.shutdown()        # fondagi havola yuborish tugashini kutamiz
        return response

    async def test_valid_webhook_marks_paid_once(self):
        body = webhook_body(self.order, self.bill_id)
        r1 = await self.post(body, sign_webhook(body, 'whd_1'))
        self.assertEqual(r1.status_code, 200)
        self.assertEqual((await self.db.get_order(self.order['id']))['status'], 'paid')
        # Takroriy yetkazish va operator qayta yuborishi (yangi delivery id, o‘sha tana id).
        r2 = await self.post(body, sign_webhook(body, 'whd_1'))
        r3 = await self.post(body, sign_webhook(body, 'whd_2'))
        self.assertTrue(r2.json()['duplicate'])
        self.assertTrue(r3.json()['duplicate'])
        self.assertEqual(len(self.bot.invites), 1)
        self.assertEqual(len(self.bot.links_sent_to(USER_ID)), 1)
        self.assertEqual(await self.db.value('SELECT COUNT(*) FROM purchases'), 1)
        self.assertEqual((await self.db.get_webhook_event('oev_1'))['status'], 'processed')

    async def test_distinct_events_for_same_payment_are_idempotent(self):
        for n in range(3):
            body = webhook_body(self.order, self.bill_id, event_id=f'oev_{n}')
            self.assertEqual((await self.post(body, sign_webhook(body, f'whd_{n}'))).status_code, 200)
        self.assertEqual(len(self.bot.invites), 1)
        self.assertEqual(await self.db.value('SELECT COUNT(*) FROM purchases'), 1)

    async def test_bad_signature_is_rejected_and_order_untouched(self):
        body = webhook_body(self.order, self.bill_id)
        headers = sign_webhook(body, secret='wbs_wrong_secret')
        self.assertEqual((await self.post(body, headers)).status_code, 401)
        tampered = body.replace(b'"live"', b'"live" ')
        self.assertEqual((await self.post(tampered, sign_webhook(body))).status_code, 401)
        headers = sign_webhook(body)
        headers['X-Checkout-Signature'] = headers['X-Checkout-Signature'].replace('v1=', 'v2=')
        self.assertEqual((await self.post(body, headers)).status_code, 401)
        self.assertEqual((await self.post(body, {'Content-Type': 'application/json'})).status_code, 401)
        self.assertEqual((await self.db.get_order(self.order['id']))['status'], 'pending')
        self.assertIsNone(await self.db.get_webhook_event('oev_1'))
        self.assertEqual(self.bot.invites, [])

    async def test_stale_timestamp_is_rejected(self):
        body = webhook_body(self.order, self.bill_id)
        r = await self.post(body, sign_webhook(body, timestamp=int(time.time()) - 3600))
        self.assertEqual(r.status_code, 401)
        self.assertEqual((await self.db.get_order(self.order['id']))['status'], 'pending')

    async def test_rotated_secret_header_with_two_signatures(self):
        body = webhook_body(self.order, self.bill_id)
        good = sign_webhook(body)
        old = sign_webhook(body, secret='wbs_old', timestamp=int(good['X-Checkout-Timestamp']))
        good['X-Checkout-Signature'] = old['X-Checkout-Signature'] + ',' + good['X-Checkout-Signature']
        self.assertEqual((await self.post(body, good)).status_code, 200)
        self.assertEqual((await self.db.get_order(self.order['id']))['status'], 'paid')

    async def test_test_environment_does_not_grant_access(self):
        body = webhook_body(self.order, self.bill_id, environment='test')
        self.assertEqual((await self.post(body, sign_webhook(body))).status_code, 200)
        self.assertEqual((await self.db.get_order(self.order['id']))['status'], 'pending')
        self.assertEqual((await self.db.get_webhook_event('oev_1'))['status'], 'ignored')
        self.assertEqual(self.bot.invites, [])

    async def test_wrong_amount_does_not_grant_access(self):
        body = webhook_body(self.order, self.bill_id, amount=1)
        self.assertEqual((await self.post(body, sign_webhook(body))).status_code, 200)
        self.assertEqual((await self.db.get_order(self.order['id']))['status'], 'review')
        self.assertEqual(self.bot.invites, [])

    async def test_failed_payment_event_is_ignored(self):
        body = webhook_body(self.order, self.bill_id, event_type='payment.failed')
        self.assertEqual((await self.post(body, sign_webhook(body))).status_code, 200)
        self.assertEqual((await self.db.get_order(self.order['id']))['status'], 'pending')

    async def test_unknown_order_and_bad_json(self):
        body = json.dumps({'id': 'oev_x', 'type': 'payment.succeeded',
                           'data': {'external_reference': 'ORD-unknown'}}).encode()
        self.assertEqual((await self.post(body, sign_webhook(body))).status_code, 200)
        self.assertEqual((await self.db.get_webhook_event('oev_x'))['error'], 'unknown_order')
        bad = b'{not json'
        self.assertEqual((await self.post(bad, sign_webhook(bad))).status_code, 400)

    async def test_health_endpoint(self):
        r = await self.client.get('/health')
        self.assertEqual(r.json(), {'status': 'ok'})


if __name__ == '__main__':
    unittest.main()
