"""Tezcheck mijozi: lokal soxta HTTP server bilan (internet va haqiqiy pul ishlatilmaydi)."""
import asyncio
import json
import unittest

from aiohttp import web
from aiohttp.test_utils import TestServer

from services.tezcheck import BillStatus, TezcheckClient, TezcheckError, Verdict, evaluate_bill, parse_created

ORDER = {'amount_uzs': 150000, 'external_reference': 'ORD-1', 'tezcheck_bill_id': 'link_1'}


def status(**over) -> BillStatus:
    values = dict(bill_id='link_1', state='exhausted', paid=True, livemode=True, environment='live',
                  amount_minor=15000000, external_reference='ORD-1', unavailability_reason='use_limit_reached',
                  payment_id='pay_1', payment_state='succeeded', payment_amount_minor=15000000,
                  payment_livemode=True, payment_environment='live', refunded_amount_minor=0)
    values.update(over)
    return BillStatus(**values)


class VerdictTests(unittest.TestCase):
    def test_paid_live(self):
        self.assertIs(evaluate_bill(ORDER, status()), Verdict.PAID)

    def test_paid_but_expired_bill_still_paid(self):
        # Hujjat: to‘langandan keyin muddati o‘tgan hisob expired bo‘ladi, lekin pul kelgan.
        self.assertIs(evaluate_bill(ORDER, status(state='expired')), Verdict.PAID)

    def test_unpaid(self):
        self.assertIs(evaluate_bill(ORDER, status(paid=False, state='active', payment_id=None)), Verdict.PENDING)
        self.assertIs(evaluate_bill(ORDER, status(paid=False, state='revoked')), Verdict.EXPIRED)

    def test_test_provider(self):
        self.assertIs(evaluate_bill(ORDER, status(livemode=False, environment='test')), Verdict.TEST)

    def test_mismatches(self):
        for over in ({'amount_minor': 100}, {'external_reference': 'ORD-2'}, {'bill_id': 'link_2'},
                     {'payment_amount_minor': 1}, {'payment_state': 'processing'}, {'refunded_amount_minor': 5},
                     {'payment_environment': 'test'}):
            with self.subTest(over=over):
                self.assertIs(evaluate_bill(ORDER, status(**over)), Verdict.MISMATCH)

    def test_payment_url_must_be_https(self):
        data = {'bill': {'id': 'link_1', 'external_reference': 'ORD-1', 'amount_minor': 100},
                'payment_url': 'http://localhost:3103/pay/x'}
        with self.assertRaises(TezcheckError):
            parse_created(data, 'ORD-1', 100)


class ClientTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.requests = []
        self.mode = 'ok'
        app = web.Application()
        app.router.add_route('*', '/api/merchant/v1/{tail:.*}', self.handler)
        self.server = TestServer(app)
        await self.server.start_server()
        base = str(self.server.make_url('/api/merchant/v1'))
        self.client = TezcheckClient(base, 'aps_secret_key', 'cdk_desk', timeout=2)
        self.addAsyncCleanup(self._cleanup)

    async def _cleanup(self):
        await self.client.close()
        await self.server.close()

    async def handler(self, request: web.Request):
        body = await request.read()
        self.requests.append({'method': request.method, 'path': request.path, 'headers': dict(request.headers),
                              'body': json.loads(body) if body else None})
        if self.mode == 'timeout':
            await asyncio.sleep(5)
        if self.mode == 'badjson':
            return web.Response(text='<html>oops</html>', content_type='text/html')
        if self.mode == '401':
            return web.json_response({'error': {'code': 'auth.unauthenticated', 'status': 401, 'retryable': False}},
                                     status=401, headers={'X-Request-Id': 'req_1'})
        if self.mode == '500' and len(self.requests) < 2:
            return web.json_response({'error': {'code': 'server.internal_error'}}, status=500)
        if request.path.endswith('/bills') and request.method == 'POST':
            payload = json.loads(body)
            return web.json_response({'data': {
                'bill': {'id': 'link_1', 'external_reference': payload['external_reference'],
                         'amount_minor': payload['amount_minor'], 'currency': 'UZS', 'state': 'active'},
                'payment_url': 'https://tezcheck.uz/pay/plk_1'}}, status=201)
        return web.json_response({'data': {
            'bill': {'id': 'link_1', 'state': 'active', 'paid': False, 'livemode': True, 'environment': None,
                     'external_reference': 'ORD-1', 'amount_minor': 15000000, 'currency': 'UZS'},
            'payment': None, 'payment_url': None}})

    async def test_create_bill_request_format(self):
        bill = await self.client.create_bill(amount_minor=15000000, title='Python — #1', external_reference='ORD-1',
                                             idempotency_key='bill-ORD-1-15000000')
        self.assertEqual((bill.bill_id, bill.payment_url), ('link_1', 'https://tezcheck.uz/pay/plk_1'))
        req = self.requests[0]
        self.assertEqual((req['method'], req['path']), ('POST', '/api/merchant/v1/bills'))
        self.assertEqual(req['headers']['Authorization'], 'Bearer aps_secret_key')
        self.assertEqual(req['headers']['X-Cash-Desk-Code'], 'cdk_desk')
        self.assertEqual(req['headers']['Idempotency-Key'], 'bill-ORD-1-15000000')
        self.assertEqual(req['body'], {'amount_minor': 15000000, 'title': 'Python — #1', 'external_reference': 'ORD-1'})
        self.assertNotIn('currency', req['body'])     # hujjat: valyuta yuborilmaydi

    async def test_get_bill_uses_post(self):
        result = await self.client.get_bill('link_1')
        self.assertFalse(result.paid)
        self.assertEqual((self.requests[0]['method'], self.requests[0]['path']), ('POST', '/api/merchant/v1/bills/link_1'))

    async def test_error_code_and_request_id(self):
        self.mode = '401'
        with self.assertRaises(TezcheckError) as ctx:
            await self.client.get_bill('link_1')
        self.assertEqual((ctx.exception.code, ctx.exception.status, ctx.exception.request_id),
                         ('auth.unauthenticated', 401, 'req_1'))
        self.assertEqual(len(self.requests), 1)       # 401 qayta urinilmaydi
        self.assertNotIn('aps_secret_key', str(ctx.exception))

    async def test_retry_on_server_error_keeps_idempotency_key(self):
        self.mode = '500'
        await self.client.create_bill(amount_minor=15000000, title='T', external_reference='ORD-1',
                                      idempotency_key='bill-ORD-1-15000000')
        self.assertEqual(len(self.requests), 2)
        self.assertEqual({r['headers']['Idempotency-Key'] for r in self.requests}, {'bill-ORD-1-15000000'})

    async def test_invalid_json(self):
        self.mode = 'badjson'
        with self.assertRaises(TezcheckError) as ctx:
            await self.client.get_bill('link_1')
        self.assertEqual(ctx.exception.code, 'invalid_response:json')

    async def test_timeout_is_handled(self):
        self.mode = 'timeout'
        self.client = TezcheckClient(str(self.server.make_url('/api/merchant/v1')), 'k', 'c', timeout=0.2)
        with self.assertRaises(TezcheckError) as ctx:
            await self.client._request('POST', '/bills/link_1', attempts=1)
        self.assertEqual(ctx.exception.code, 'network_error')
        await self.client.close()


if __name__ == '__main__':
    unittest.main()
