import uuid
from datetime import datetime, timezone

from pymongo import AsyncMongoClient, ReturnDocument
from pymongo.errors import DuplicateKeyError
from pymongo.read_concern import ReadConcern
from pymongo.write_concern import WriteConcern


def now():
    return datetime.now(timezone.utc).replace(tzinfo=None).isoformat(sep=' ', timespec='seconds')


class Database:
    def __init__(self, uri, name):
        self.uri, self.name = uri, name

    async def open(self):
        self.client = AsyncMongoClient(self.uri, serverSelectionTimeoutMS=10000,
                                       connectTimeoutMS=10000, socketTimeoutMS=20000)
        self.db = self.client.get_database(self.name, write_concern=WriteConcern('majority'))
        try:
            await self.ping()
            for collection in ('courses', 'orders', 'purchases', 'admin_logs'):
                await self.db[collection].create_index('id', unique=True)
            await self.db.orders.create_index([('user_id', 1), ('course_id', 1)], unique=True)
            await self.db.orders.create_index('external_reference', unique=True)
            for field in ('bill_id', 'payment_id'):
                await self.db.orders.create_index(field, unique=True,
                    partialFilterExpression={field: {'$type': 'string'}})
            await self.db.orders.create_index([('status', 1), ('last_checked', 1)])
            await self.db.courses.create_index([('deleted', 1), ('active', 1), ('id', 1)])
            await self.db.purchases.create_index([('user_id', 1), ('course_id', 1)], unique=True)
            await self.db.purchases.create_index('order_id', unique=True)
            await self.db.purchases.create_index('invite_link', unique=True,
                partialFilterExpression={'invite_link': {'$type': 'string'}})
            await self.db.admin_logs.create_index('created_at')
            await self.db.funnel.create_index([('event', 1), ('user_id', 1), ('course_id', 1)], unique=True)
        except BaseException:
            await self.client.close()
            raise

    async def close(self):
        await self.client.close()

    async def ping(self):
        await self.client.admin.command('ping')

    async def next_id(self, collection):
        counter = await self.db.counters.find_one_and_update({'_id': collection},
            {'$inc': {'value': 1}}, upsert=True, return_document=ReturnDocument.AFTER)
        return counter['value']

    async def one(self, collection, query):
        return await self.db[collection].find_one(query, {'_id': 0})

    async def rows(self, collection, query=None, sort=None, skip=0, limit=0):
        cursor = self.db[collection].find(query or {}, {'_id': 0})
        if sort:
            cursor = cursor.sort(sort)
        if skip:
            cursor = cursor.skip(skip)
        if limit:
            cursor = cursor.limit(limit)
        return [row async for row in cursor]

    async def update(self, collection, query, values, many=False):
        method = self.db[collection].update_many if many else self.db[collection].update_one
        return await method(query, {'$set': values})

    async def audit(self, admin, action, details):
        await self.db.admin_logs.insert_one({'id': await self.next_id('admin_logs'),
            'admin_id': admin, 'action': action, 'details': details, 'created_at': now()})

    async def transaction(self, callback):
        async with self.client.start_session() as session:
            return await session.with_transaction(callback, read_concern=ReadConcern('snapshot'),
                                                  write_concern=WriteConcern('majority'))

    async def course_change(self, admin, action, values=None, cid=None):
        values = values or {}
        ident = await self.next_id('courses') if action == 'add' else cid
        log_id = await self.next_id('admin_logs')

        async def change(session):
            if action == 'add':
                await self.db.courses.insert_one({'id': ident, **values, 'active': 1,
                    'deleted': 0, 'created_at': now()}, session=session)
            else:
                query = {'id': ident, 'deleted': 0}
                if action == 'toggle':
                    result = await self.db.courses.update_one(query,
                        [{'$set': {'active': {'$subtract': [1, '$active']}}}], session=session)
                else:
                    data = {'deleted': 1, 'active': 0} if action == 'delete' else values
                    result = await self.db.courses.update_one(query, {'$set': data}, session=session)
                if not result.matched_count:
                    raise ValueError('Kurs topilmadi.')
            await self.db.admin_logs.insert_one({'id': log_id, 'admin_id': admin,
                'action': action, 'details': {'course_id': ident, 'values': values},
                'created_at': now()}, session=session)
            return ident

        return await self.transaction(change)

    async def order(self, user, course):
        query = {'user_id': user, 'course_id': course}
        existing = await self.one('orders', query)
        if existing:
            return existing
        c = await self.one('courses', {'id': course, 'active': 1, 'deleted': 0})
        if not c:
            raise ValueError('Kurs hozir sotuvda emas.')
        order = {'id': await self.next_id('orders'), **query, 'amount': c['price'],
            'channel_chat_id': c['channel_chat_id'], 'title': c['name'],
            'external_reference': 'ORD-' + uuid.uuid4().hex, 'status': 'creating',
            'bill_id': None, 'payment_url': None, 'payment_id': None,
            'created_at': now(), 'paid_at': None, 'last_checked': ''}
        try:
            await self.db.orders.insert_one(order)
        except DuplicateKeyError:
            existing = await self.one('orders', query)
            if not existing:
                raise
            return existing
        order.pop('_id', None)
        return order

    async def mark_paid(self, order, payment_id):
        purchase_id = await self.next_id('purchases')

        async def paid(session):
            current = await self.db.orders.find_one({'id': order['id']}, session=session)
            if not current or current['user_id'] != order['user_id'] or current['course_id'] != order['course_id']:
                raise ValueError('Buyurtma topilmadi.')
            if current.get('payment_id') and current['payment_id'] != payment_id:
                raise ValueError('Buyurtmaning to‘lov IDsi o‘zgardi.')
            await self.db.orders.update_one({'id': order['id']}, {'$set': {
                'status': 'paid', 'payment_id': payment_id, 'paid_at': current.get('paid_at') or now()}}, session=session)
            await self.db.purchases.update_one({'order_id': order['id']}, {'$setOnInsert': {
                'id': purchase_id, 'user_id': order['user_id'], 'course_id': order['course_id'],
                'order_id': order['id'], 'invite_link': None, 'status': 'waiting',
                'expires_at': None, 'notified': 0, 'created_at': now()}}, upsert=True, session=session)

        await self.transaction(paid)

    async def purchase_by_link(self, link):
        p = await self.one('purchases', {'invite_link': link})
        if p:
            o = await self.one('orders', {'id': p['order_id'], 'status': 'paid'})
            if o:
                return {**p, 'channel_chat_id': o['channel_chat_id']}
        return None

    async def orders_page(self, page):
        rows = await self.rows('orders', sort=[('id', -1)], skip=page * 10, limit=10)
        for order in rows:
            p = await self.one('purchases', {'order_id': order['id']})
            order['access'] = p['status'] if p else None
        return rows

    async def track(self, event, user_id, course_id=0):
        query = {'event': event, 'user_id': user_id, 'course_id': course_id}
        await self.db.funnel.update_one(query, {'$setOnInsert': {'first_at': now()},
            '$set': {'last_at': now()}, '$inc': {'hits': 1}}, upsert=True)

    async def funnel_report(self):
        stages = [row async for row in await self.db.funnel.aggregate([
            {'$group': {'_id': '$event', 'unique': {'$sum': 1}, 'hits': {'$sum': '$hits'}}}])]
        conversion = [row async for row in await self.db.funnel.aggregate([
            {'$match': {'event': 'course_view'}},
            {'$lookup': {'from': 'orders', 'let': {'user': '$user_id', 'course': '$course_id'},
                'pipeline': [{'$match': {'$expr': {'$and': [
                    {'$eq': ['$user_id', '$$user']}, {'$eq': ['$course_id', '$$course']},
                    {'$eq': ['$status', 'paid']}]}}}], 'as': 'paid_orders'}},
            {'$group': {'_id': None, 'views': {'$sum': 1}, 'buyers': {'$sum': {
                '$cond': [{'$gt': [{'$size': '$paid_orders'}, 0]}, 1, 0]}}}}
        ])]
        return {row['_id']: row for row in stages}, conversion[0] if conversion else {'views': 0, 'buyers': 0}

    async def stats(self):
        courses = await self.db.courses.count_documents({'deleted': 0})
        count = await self.db.orders.count_documents({})
        result = [x async for x in await self.db.orders.aggregate([
            {'$match': {'status': 'paid'}}, {'$group': {'_id': None,
                'paid': {'$sum': 1}, 'revenue': {'$sum': '$amount'}}}])]
        return {'courses': courses, 'n': count, 'paid': result[0]['paid'] if result else 0,
                'revenue': result[0]['revenue'] if result else 0}
