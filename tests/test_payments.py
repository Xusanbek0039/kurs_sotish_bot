import asyncio
import tempfile
import unittest
from pathlib import Path

from database import Database
from tezcheck import PaymentError, Tezcheck


class PaymentTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Database(str(Path(self.tmp.name) / 'test.db'))
        await self.db.open()
        self.cid = await self.db.course_change(1, 'add',
            'INSERT INTO courses(name,description,price,channel_chat_id) VALUES(?,?,?,?)',
            ('Python', 'Darslar', 150000, -1001234567890))

    async def asyncTearDown(self):
        await self.db.close()
        self.tmp.cleanup()

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
        self.assertEqual(len(await self.db.rows('SELECT * FROM purchases')), 1)
        self.assertEqual((await self.db.order(42, self.cid))['status'], 'paid')

    async def test_order_snapshots_survive_course_edit_and_delete(self):
        o = await self.db.order(42, self.cid)
        await self.db.course_change(1, 'edit', 'UPDATE courses SET price=200000,channel_chat_id=-100999,deleted=1 WHERE id=?', (self.cid,))
        same = await self.db.order(42, self.cid)
        self.assertEqual(same['amount'], 150000)
        self.assertEqual(same['channel_chat_id'], o['channel_chat_id'])

    async def test_payment_validation(self):
        o = await self.db.order(42, self.cid)
        o['bill_id'] = 'link_1'
        client = object.__new__(Tezcheck)
        bill = {'id': 'link_1', 'external_reference': o['external_reference'], 'amount_minor': 15000000,
                'currency': 'UZS', 'paid': True, 'livemode': True, 'environment': 'live', 'state': 'expired'}
        payment = {'id': 'pay_1', 'state': 'succeeded', 'amount_minor': 15000000, 'currency': 'UZS',
                   'livemode': True, 'environment': 'live', 'refunded_amount_minor': 0}

        async def response(*args, **kwargs):
            return {'bill': bill, 'payment': payment}

        client.request = response
        self.assertEqual(await client.check(o), (True, 'pay_1'))
        bill['livemode'] = False
        with self.assertRaises(PaymentError):
            await client.check(o)
        bill['livemode'] = True
        payment['amount_minor'] = 1
        with self.assertRaises(PaymentError):
            await client.check(o)
        payment['amount_minor'] = 15000000
        bill['external_reference'] = 'OTHER'
        with self.assertRaises(PaymentError):
            await client.check(o)

    async def test_unpaid_never_passes(self):
        o = await self.db.order(42, self.cid)
        o['bill_id'] = 'link_1'
        client = object.__new__(Tezcheck)

        async def response(*args, **kwargs):
            return {'bill': {'id': 'link_1', 'external_reference': o['external_reference'], 'amount_minor': 15000000,
                             'currency': 'UZS', 'paid': False, 'livemode': True}}

        client.request = response
        self.assertEqual(await client.check(o), (False, None))
        self.assertFalse(await self.db.rows('SELECT * FROM purchases'))


if __name__ == '__main__':
    unittest.main()
