import asyncio
import json
from urllib.parse import quote, urlparse

import aiohttp


class PaymentError(Exception):
    pass


class Tezcheck:
    def __init__(self, config):
        self.config = config
        self.session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=20), headers={
            'Authorization': 'Bearer ' + config.api_key,
            'X-Cash-Desk-Code': config.desk, 'Accept': 'application/json'})

    async def close(self):
        await self.session.close()

    async def request(self, method, path, body=None, key=None):
        headers = {'Content-Type': 'application/json'}
        if key:
            headers['Idempotency-Key'] = key
        try:
            async with self.session.request(method, self.config.base_url + path,
                    data=json.dumps(body).encode() if body is not None else None,
                    headers=headers, allow_redirects=False) as response:
                if not 200 <= response.status < 300:
                    raise PaymentError(f'Tezcheck HTTP {response.status}. Keyinroq qayta tekshiring yoki adminga murojaat qiling.')
                payload = await response.json()
                if not isinstance(payload, dict) or not isinstance(payload.get('data'), dict):
                    raise PaymentError('Tezcheck javobi kutilgan formatda emas.')
                return payload['data']
        except (aiohttp.ClientError, asyncio.TimeoutError, ValueError):
            raise PaymentError('Tezcheck bilan aloqa vaqtincha uzildi. Keyinroq qayta urinib ko‘ring.') from None

    @staticmethod
    def validate_bill(bill, order, existing=False):
        if not isinstance(bill, dict) or not isinstance(bill.get('id'), str) or not bill['id']:
            raise PaymentError('Hisob identifikatori noto‘g‘ri.')
        if (bill.get('external_reference') != order['external_reference'] or
                type(bill.get('amount_minor')) is not int or bill['amount_minor'] != order['amount'] * 100 or
                bill.get('currency') != 'UZS' or (existing and bill['id'] != order['bill_id'])):
            raise PaymentError('Hisob ma’lumotlari buyurtmaga mos kelmadi. Adminga murojaat qiling.')

    async def create(self, order):
        data = await self.request('POST', '/bills', {
            'amount_minor': order['amount'] * 100, 'title': order['title'],
            'external_reference': order['external_reference']}, order['external_reference'])
        self.validate_bill(data.get('bill'), order)
        url = data.get('payment_url')
        if not isinstance(url, str) or urlparse(url).scheme != 'https' or not urlparse(url).hostname:
            raise PaymentError('Xavfsiz payment_url qaytmadi. Adminga murojaat qiling.')
        return data

    async def check(self, order):
        data = await self.request('POST', '/bills/' + quote(order['bill_id'], safe=''))
        bill = data.get('bill')
        self.validate_bill(bill, order, True)
        if type(bill.get('paid')) is not bool or type(bill.get('livemode')) is not bool:
            raise PaymentError('To‘lov holati javobda yetishmayapti.')
        if not bill['paid']:
            return False, None
        if not bill['livemode'] or bill.get('environment') != 'live':
            raise PaymentError('Bu test to‘lovi. Haqiqiy kursga kirish berilmadi; adminga murojaat qiling.')
        payment = data.get('payment')
        if (not isinstance(payment, dict) or payment.get('state') != 'succeeded' or
            payment.get('livemode') is not True or payment.get('environment') != 'live' or
            type(payment.get('amount_minor')) is not int or payment['amount_minor'] != order['amount'] * 100 or
            payment.get('currency') != 'UZS' or payment.get('refunded_amount_minor') != 0 or
            not isinstance(payment.get('id'), str) or not payment['id']):
            raise PaymentError('To‘lovni xavfsiz tasdiqlash imkoni bo‘lmadi. Adminga murojaat qiling.')
        return True, payment['id']
