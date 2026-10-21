import asyncio
import os
import unittest
import uuid

from pymongo.errors import DuplicateKeyError

from database import Database
from tezcheck import PaymentError, Tezcheck


class PaymentValidationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.order = {'id': 1, 'amount': 150000, 'external_reference': 'ORD-test', 'bill_id': 'link_1'}
        self.bill = {'id': 'link_1', 'external_reference': 'ORD-test', 'amount_minor': 15000000,
                     'currency': 'UZS', 'paid': True, 'livemode': True, 'environment': 'live', 'state': 'expired'}
        self.payment = {'id': 'pay_1', 'state': 'succeeded', 'amount_minor': 15000000, 'currency': 'UZS',
                        'livemode': True, 'environment': 'live', 'refunded_amount_minor': 0}
        self.client = object.__new__(Tezcheck)

        async def response(*args, **kwargs):
            return {'bill': self.bill, 'payment': self.payment}

        self.client.request = response

    async def test_live_payment_passes_even_if_bill_expired(self):
        self.assertEqual(await self.client.check(self.order), (True, 'pay_1'))

    async def test_unpaid_never_passes(self):
        self.bill['paid'] = False
        self.assertEqual(await self.client.check(self.order), (False, None))

    async def test_test_payment_is_rejected(self):
        self.bill['livemode'] = False
        with self.assertRaises(PaymentError):
            await self.client.check(self.order)

    async def test_wrong_amount_is_rejected(self):
        self.payment['amount_minor'] = 1
        with self.assertRaises(PaymentError):
            await self.client.check(self.order)

    async def test_wrong_reference_is_rejected(self):
        self.bill['external_reference'] = 'OTHER'
        with self.assertRaises(PaymentError):
            await self.client.check(self.order)


@unittest.skipUnless(os.getenv('MONGODB_TEST_URI'), 'MONGODB_TEST_URI berilmagan: Atlas integratsiya testlari o‘tkazib yuborildi.')
class MongoDBTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        # Never use the configured production database. Each test owns a new random test DB.
        self.db = Database(os.environ['MONGODB_TEST_URI'], 'kurs_bot_test_' + uuid.uuid4().hex)
        await self.db.open()
        self.addAsyncCleanup(self.cleanup_database)
        self.cid = await self.db.course_change(1, 'add', {
            'name': 'Python', 'description': 'Darslar', 'price': 150000, 'channel_chat_id': -1001234567890})

    async def cleanup_database(self):
        try:
            await self.db.client.drop_database(self.db.name)
        finally:
            await self.db.close()

    async def test_concurrent_checkout_and_restart(self):
        orders = await asyncio.gather(*(self.db.order(42, self.cid) for _ in range(10)))
        self.assertEqual(len({o['id'] for o in orders}), 1)
        await self.db.close()
        await self.db.open()
        self.assertEqual((await self.db.order(42, self.cid))['external_reference'], orders[0]['external_reference'])

    async def test_paid_is_atomic_and_idempotent(self):
        o = await self.db.order(42, self.cid)
        await self.db.mark_paid(o, 'pay_unique')
        await self.db.mark_paid(o, 'pay_unique')
        self.assertEqual(len(await self.db.rows('purchases')), 1)
        self.assertEqual((await self.db.order(42, self.cid))['status'], 'paid')
        await self.db.update('purchases', {'order_id': o['id']}, {'invite_link': 'https://t.me/+saved', 'status': 'ready'})
        await self.db.mark_paid(o, 'pay_unique')
        self.assertEqual((await self.db.one('purchases', {'order_id': o['id']}))['invite_link'], 'https://t.me/+saved')

    async def test_duplicate_payment_rolls_back(self):
        first = await self.db.order(42, self.cid)
        second = await self.db.order(43, self.cid)
        await self.db.mark_paid(first, 'pay_unique')
        with self.assertRaises(DuplicateKeyError):
            await self.db.mark_paid(second, 'pay_unique')
        self.assertEqual((await self.db.one('orders', {'id': second['id']}))['status'], 'creating')
        self.assertIsNone(await self.db.one('purchases', {'order_id': second['id']}))

    async def test_purchase_failure_rolls_back_paid_status(self):
        o = await self.db.order(42, self.cid)
        await self.db.db.purchases.insert_one({'id': 1000, 'user_id': 42, 'course_id': self.cid, 'order_id': -1})
        with self.assertRaises(DuplicateKeyError):
            await self.db.mark_paid(o, 'pay_unique')
        self.assertEqual((await self.db.one('orders', {'id': o['id']}))['status'], 'creating')

    async def test_order_snapshots_survive_course_edit_and_delete(self):
        o = await self.db.order(42, self.cid)
        await self.db.course_change(1, 'edit', {'price': 200000, 'channel_chat_id': -100999}, cid=self.cid)
        await self.db.course_change(1, 'delete', cid=self.cid)
        same = await self.db.order(42, self.cid)
        self.assertEqual(same['amount'], 150000)
        self.assertEqual(same['channel_chat_id'], o['channel_chat_id'])
        self.assertEqual(len(await self.db.rows('admin_logs')), 3)

    async def test_invite_claim_is_atomic(self):
        o = await self.db.order(42, self.cid)
        await self.db.mark_paid(o, 'pay_unique')
        results = await asyncio.gather(*(self.db.update('purchases',
            {'order_id': o['id'], 'status': 'waiting'}, {'status': 'creating'}) for _ in range(10)))
        self.assertEqual(sum(result.modified_count for result in results), 1)

    async def test_funnel_deduplicates_views_and_matches_paid_order(self):
        await self.db.track('course_view', 42, self.cid)
        await self.db.track('course_view', 42, self.cid)
        await self.db.track('checkout', 42, self.cid)
        order = await self.db.order(42, self.cid)
        await self.db.mark_paid(order, 'pay_funnel')
        stages, conversion = await self.db.funnel_report()
        self.assertEqual(stages['course_view']['unique'], 1)
        self.assertEqual(stages['course_view']['hits'], 2)
        self.assertEqual(conversion['views'], 1)
        self.assertEqual(conversion['buyers'], 1)


if __name__ == '__main__':
    unittest.main()
