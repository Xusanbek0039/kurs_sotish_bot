import asyncio
import sqlite3
import unittest

from database import Database
from tests.helpers import ADMIN_ID, CHANNEL_ID, USER_ID, make_db, seed


class DatabaseTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.db, self.tmp = await make_db()
        self.addAsyncCleanup(self._cleanup)
        self.cid = await seed(self.db)

    async def _cleanup(self):
        await self.db.close()
        self.tmp.cleanup()

    # ------------------------------------------------------------- kurs CRUD
    async def test_course_crud_and_audit(self):
        course = await self.db.get_course(self.cid)
        self.assertEqual(course['name_uz'], 'Python kursi')
        self.assertEqual(course['price_uzs'], 150000)
        self.assertTrue(course['is_active'])

        self.assertTrue(await self.db.update_course(ADMIN_ID, self.cid, 'price_uzs', 200000))
        self.assertTrue(await self.db.update_course(ADMIN_ID, self.cid, 'description_ru', ''))
        self.assertEqual((await self.db.get_course(self.cid))['price_uzs'], 200000)
        self.assertFalse(await self.db.toggle_course(ADMIN_ID, self.cid))
        self.assertEqual((await self.db.list_courses(0))[1], 0)          # nofaol — katalogda yo‘q
        self.assertTrue(await self.db.toggle_course(ADMIN_ID, self.cid))
        with self.assertRaises(ValueError):
            await self.db.update_course(ADMIN_ID, self.cid, 'is_deleted; DROP TABLE courses', 1)

        logs, total = await self.db.admin_logs(0)
        self.assertEqual(total, 5)   # add, edit, edit, toggle, toggle
        self.assertEqual({r['action'] for r in logs}, {'course_add', 'course_edit', 'course_toggle'})

    async def test_course_requires_a_name_and_positive_price(self):
        with self.assertRaises(sqlite3.IntegrityError):
            await self.db.add_course(ADMIN_ID, {'name_uz': '', 'name_ru': '', 'price_uzs': 1000, 'channel_chat_id': 1})
        with self.assertRaises(sqlite3.IntegrityError):
            await self.db.add_course(ADMIN_ID, {'name_uz': 'X', 'price_uzs': 0, 'channel_chat_id': 1})

    async def test_delete_is_soft_and_keeps_history(self):
        kind, order = await self.db.get_or_create_open_order(USER_ID, self.cid)
        await self.db.set_order_bill(order['id'], 'link_1', 'https://pay.example/1')
        await self.db.mark_paid(order['id'], 'pay_1', 'live')
        self.assertTrue(await self.db.delete_course(ADMIN_ID, self.cid))
        self.assertIsNone(await self.db.get_course(self.cid))
        self.assertIsNotNone(await self.db.get_course(self.cid, include_deleted=True))
        self.assertEqual((await self.db.get_order(order['id']))['status'], 'paid')
        self.assertIsNotNone(await self.db.purchase_for(USER_ID, self.cid))
        kind, _ = await self.db.get_or_create_open_order(USER_ID + 1, self.cid)
        self.assertEqual(kind, 'unavailable')

    # ------------------------------------------------------------- buyurtma
    async def test_open_order_is_unique_under_concurrency(self):
        results = await asyncio.gather(*(self.db.get_or_create_open_order(USER_ID, self.cid) for _ in range(10)))
        ids = {row['id'] for _, row in results}
        self.assertEqual(len(ids), 1)
        self.assertEqual(sum(kind == 'created' for kind, _ in results), 1)
        order = results[0][1]
        self.assertTrue(order['external_reference'].startswith('ORD-'))
        self.assertEqual(order['amount_uzs'], 150000)

    async def test_inactive_course_cannot_be_ordered(self):
        await self.db.toggle_course(ADMIN_ID, self.cid)
        self.assertEqual((await self.db.get_or_create_open_order(USER_ID, self.cid))[0], 'unavailable')
        self.assertEqual((await self.db.get_or_create_open_order(USER_ID, 999))[0], 'unavailable')

    async def test_expired_order_allows_new_order(self):
        _, first = await self.db.get_or_create_open_order(USER_ID, self.cid)
        await self.db.set_order_bill(first['id'], 'link_1', 'https://pay.example/1')
        await self.db.set_order_status(first['id'], 'expired', ('pending',))
        kind, second = await self.db.get_or_create_open_order(USER_ID, self.cid)
        self.assertEqual(kind, 'created')
        self.assertNotEqual(first['id'], second['id'])
        self.assertNotEqual(first['external_reference'], second['external_reference'])

    async def test_mark_paid_is_atomic_and_idempotent(self):
        _, order = await self.db.get_or_create_open_order(USER_ID, self.cid)
        first = await self.db.mark_paid(order['id'], 'pay_1', 'live')
        second = await self.db.mark_paid(order['id'], 'pay_1', 'live')
        self.assertTrue(first.newly_paid)
        self.assertFalse(second.newly_paid)
        self.assertEqual(first.purchase_id, second.purchase_id)
        self.assertEqual(await self.db.value('SELECT COUNT(*) FROM purchases'), 1)
        with self.assertRaises(ValueError):
            await self.db.mark_paid(order['id'], 'pay_OTHER', 'live')
        # Sotib olingan kurs uchun yangi buyurtma ochilmaydi.
        self.assertEqual((await self.db.get_or_create_open_order(USER_ID, self.cid))[0], 'purchased')

    async def test_second_paid_order_for_same_course_is_flagged(self):
        _, a = await self.db.get_or_create_open_order(USER_ID, self.cid)
        await self.db.set_order_status(a['id'], 'expired', ('new',))
        _, b = await self.db.get_or_create_open_order(USER_ID, self.cid)
        await self.db.mark_paid(b['id'], 'pay_b', 'live')
        late = await self.db.mark_paid(a['id'], 'pay_a', 'live')   # muddati o‘tgan hisob baribir to‘langan
        self.assertTrue(late.duplicate)
        self.assertIsNone(late.purchase_id)
        self.assertEqual(await self.db.value('SELECT COUNT(*) FROM purchases'), 1)

    async def test_claim_purchase_only_once(self):
        _, order = await self.db.get_or_create_open_order(USER_ID, self.cid)
        paid = await self.db.mark_paid(order['id'], 'pay_1', 'live')
        claims = await asyncio.gather(*(self.db.claim_purchase(paid.purchase_id, ('pending_invite',)) for _ in range(5)))
        self.assertEqual(sum(c is not None for c in claims), 1)
        self.assertEqual(await self.db.reset_stuck_deliveries(), 1)
        self.assertEqual((await self.db.get_purchase(paid.purchase_id))['status'], 'pending_invite')

    # ------------------------------------------------------------- foydalanuvchilar
    async def test_users_listing_search_block(self):
        for i in range(25):
            await self.db.touch_user(5000 + i, f'user_{i}', f'Name{i}')
        rows, total = await self.db.list_users('all', 'new', 0, 10)
        self.assertEqual(total, 27)
        self.assertEqual(len(rows), 10)
        self.assertEqual(len((await self.db.list_users('all', 'active', 2, 10))[0]), 7)
        self.assertEqual([u['id'] for u in await self.db.search_users('@user_7')], [5007])
        self.assertEqual([u['id'] for u in await self.db.search_users(str(USER_ID))], [USER_ID])
        self.assertEqual(await self.db.search_users('%'), [])          # LIKE belgilari ekranlangan
        _, order = await self.db.get_or_create_open_order(USER_ID, self.cid)
        await self.db.mark_paid(order['id'], 'pay_1', 'live')
        self.assertEqual((await self.db.list_users('buyers', 'new', 0))[1], 1)
        self.assertEqual((await self.db.list_users('non_buyers', 'new', 0))[1], 26)
        self.assertTrue(await self.db.set_blocked(ADMIN_ID, 5001, True))
        self.assertEqual((await self.db.list_users('blocked', 'new', 0))[1], 1)
        summary = await self.db.users_summary()
        self.assertEqual(summary['total'], 27)
        self.assertEqual(summary['active_today'], 27)
        self.assertEqual(summary['buyers'], 1)

    async def test_profile_and_views(self):
        await self.db.record_course_view(USER_ID, self.cid)
        await self.db.record_course_view(USER_ID, self.cid)
        profile = await self.db.user_profile(USER_ID)
        self.assertEqual(profile['views'][0]['views'], 2)
        self.assertEqual(profile['language'], 'uz')

    async def test_data_survives_restart(self):
        path = self.db.path
        await self.db.close()
        reopened = Database(path)
        await reopened.connect()
        self.db = reopened
        course = await reopened.get_course(self.cid)
        self.assertEqual(course['channel_chat_id'], CHANNEL_ID)


if __name__ == '__main__':
    unittest.main()
