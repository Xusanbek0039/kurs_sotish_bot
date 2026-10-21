"""Tezcheck Merchant API mijozi.

Faqat rasmiy hujjatdagi (https://tezcheck.uz/api-docs) endpoint va maydonlardan foydalaniladi:
  GET  /me             — kalit va kassa mosligini tekshirish
  POST /bills          — hisob yaratish (Idempotency-Key bilan)
  POST /bills/{bill}   — hisob va to‘lov holatini o‘qish
Webhook imzosi: HMAC-SHA256("{timestamp}.{delivery_id}.{raw_body}"), sarlavha "v1=<hex>[,v1=<hex>]".
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import time
from dataclasses import dataclass
from enum import Enum
from urllib.parse import quote, urlparse

import aiohttp

log = logging.getLogger(__name__)

SIGNATURE_WINDOW_SECONDS = 300
RETRYABLE_HTTP = {429, 500, 502, 503, 504}


class TezcheckError(Exception):
    """API xatosi. `code` — Tezcheck’ning barqaror xato kodi yoki ichki kod."""

    def __init__(self, code: str, status: int | None = None, retryable: bool = False, request_id: str | None = None):
        super().__init__(code)
        self.code, self.status, self.retryable, self.request_id = code, status, retryable, request_id

    def __str__(self) -> str:
        parts = [self.code]
        if self.status:
            parts.append(f'HTTP {self.status}')
        if self.request_id:
            parts.append(f'request_id={self.request_id}')
        return ' '.join(parts)


@dataclass(frozen=True)
class CreatedBill:
    bill_id: str
    payment_url: str


@dataclass(frozen=True)
class BillStatus:
    bill_id: str
    state: str
    paid: bool
    livemode: bool
    environment: str | None
    amount_minor: int
    external_reference: str | None
    unavailability_reason: str | None
    payment_id: str | None
    payment_state: str | None
    payment_amount_minor: int | None
    payment_livemode: bool | None
    payment_environment: str | None
    refunded_amount_minor: int | None


class Verdict(str, Enum):
    PAID = 'paid'                # haqiqiy pul tushgan — kursni berish mumkin
    PENDING = 'pending'          # hali to‘lanmagan
    EXPIRED = 'expired'          # hisob o‘lgan va to‘lanmagan
    TEST = 'test'                # test provayderi orqali — haqiqiy pul emas
    MISMATCH = 'mismatch'        # summa/ma’lumot buyurtmaga mos emas — admin tekshiradi


DEAD_BILL_STATES = {'expired', 'exhausted', 'revoked'}


def evaluate_bill(order: dict, bill: BillStatus) -> Verdict:
    """Hisob holatidan qaror chiqaradi. Hujjat bo‘yicha: faqat `paid` va `livemode` true bo‘lsa jo‘nating."""
    expected = order['amount_uzs'] * 100
    if (bill.bill_id != order.get('tezcheck_bill_id') or bill.amount_minor != expected
            or bill.external_reference != order['external_reference']):
        return Verdict.MISMATCH
    if not bill.paid:
        return Verdict.EXPIRED if bill.state in DEAD_BILL_STATES else Verdict.PENDING
    if not bill.livemode or bill.environment != 'live':
        return Verdict.TEST
    if (bill.payment_state != 'succeeded' or not bill.payment_id or bill.payment_livemode is not True
            or bill.payment_environment != 'live' or bill.payment_amount_minor != expected
            or (bill.refunded_amount_minor or 0) != 0):
        return Verdict.MISMATCH
    return Verdict.PAID


def verify_webhook_signature(secret: str, raw_body: bytes, timestamp: str, delivery_id: str, header: str,
                             now: float | None = None) -> bool:
    """Hujjatdagi Python namunasi bilan bir xil tekshiruv (doimiy vaqtli solishtirish)."""
    if not secret or not timestamp or not delivery_id or not header:
        return False
    try:
        sent_at = int(timestamp)
    except ValueError:
        return False
    if abs((now if now is not None else time.time()) - sent_at) > SIGNATURE_WINDOW_SECONDS:
        return False
    signed = timestamp.encode() + b'.' + delivery_id.encode() + b'.' + raw_body
    expected = 'v1=' + hmac.new(secret.encode(), signed, hashlib.sha256).hexdigest()
    # Kalit almashtirilganda sarlavhada ikkita imzo keladi — istalgan biri mos kelsa yetarli.
    return any(hmac.compare_digest(candidate.strip(), expected) for candidate in header.split(','))


def _require(cond: bool, what: str) -> None:
    if not cond:
        raise TezcheckError(f'invalid_response:{what}')


def _https(url) -> bool:
    if not isinstance(url, str):
        return False
    parsed = urlparse(url)
    return parsed.scheme == 'https' and bool(parsed.hostname)


def parse_created(data: dict, external_reference: str, amount_minor: int) -> CreatedBill:
    bill = data.get('bill')
    _require(isinstance(bill, dict), 'bill')
    _require(isinstance(bill.get('id'), str) and bill['id'] != '', 'bill.id')
    _require(bill.get('external_reference') == external_reference, 'bill.external_reference')
    _require(type(bill.get('amount_minor')) is int and bill['amount_minor'] == amount_minor, 'bill.amount_minor')
    _require(_https(data.get('payment_url')), 'payment_url')
    return CreatedBill(bill['id'], data['payment_url'])


def parse_status(data: dict) -> BillStatus:
    bill = data.get('bill')
    _require(isinstance(bill, dict), 'bill')
    _require(isinstance(bill.get('id'), str) and bill['id'] != '', 'bill.id')
    _require(type(bill.get('paid')) is bool and type(bill.get('livemode')) is bool, 'bill.paid')
    _require(type(bill.get('amount_minor')) is int, 'bill.amount_minor')
    payment = data.get('payment') if isinstance(data.get('payment'), dict) else {}
    return BillStatus(
        bill_id=bill['id'], state=str(bill.get('state') or ''), paid=bill['paid'], livemode=bill['livemode'],
        environment=bill.get('environment'), amount_minor=bill['amount_minor'],
        external_reference=bill.get('external_reference'),
        unavailability_reason=bill.get('unavailability_reason'),
        payment_id=payment.get('id') if isinstance(payment.get('id'), str) else None,
        payment_state=payment.get('state'),
        payment_amount_minor=payment.get('amount_minor') if type(payment.get('amount_minor')) is int else None,
        payment_livemode=payment.get('livemode') if type(payment.get('livemode')) is bool else None,
        payment_environment=payment.get('environment'),
        refunded_amount_minor=payment.get('refunded_amount_minor')
        if type(payment.get('refunded_amount_minor')) is int else None,
    )


class TezcheckClient:
    def __init__(self, base_url: str, api_key: str, cash_desk_code: str, timeout: float = 15.0):
        self._base_url = base_url.rstrip('/')
        self._headers = {'Authorization': f'Bearer {api_key}', 'X-Cash-Desk-Code': cash_desk_code,
                         'Accept': 'application/json'}
        self._timeout = aiohttp.ClientTimeout(total=timeout)
        self._session: aiohttp.ClientSession | None = None

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(timeout=self._timeout, headers=self._headers)
        return self._session

    async def close(self) -> None:
        if self._session is not None and not self._session.closed:
            await self._session.close()

    async def _request(self, method: str, path: str, body: dict | None = None,
                       idempotency_key: str | None = None, attempts: int = 3) -> dict:
        """So‘rov yuboradi va `data` obyektini qaytaradi. Faqat xavfsiz holatlarda qayta urinadi."""
        session = await self._get_session()
        headers = {}
        payload = None
        if body is not None or method == 'POST':
            payload = json.dumps(body or {}, ensure_ascii=False).encode()
            headers['Content-Type'] = 'application/json'
        if idempotency_key:
            headers['Idempotency-Key'] = idempotency_key
        # Idempotency-Key bo‘lmagan POST faqat o‘qish uchun (POST /bills/{bill}) — qayta urinish xavfsiz.
        last_error: TezcheckError | None = None
        for attempt in range(attempts):
            if attempt:
                await asyncio.sleep(min(2 ** attempt, 5))
            try:
                async with session.request(method, self._base_url + path, data=payload, headers=headers,
                                           allow_redirects=False) as resp:
                    request_id = resp.headers.get('X-Request-Id')
                    try:
                        parsed = await resp.json(content_type=None)
                    except (json.JSONDecodeError, aiohttp.ContentTypeError, UnicodeDecodeError):
                        parsed = None
                    if 200 <= resp.status < 300:
                        if not isinstance(parsed, dict) or not isinstance(parsed.get('data'), (dict, list)):
                            raise TezcheckError('invalid_response:json', resp.status, False, request_id)
                        return parsed['data']
                    err = parsed.get('error') if isinstance(parsed, dict) else None
                    code = err.get('code') if isinstance(err, dict) and isinstance(err.get('code'), str) else 'http_error'
                    retryable = resp.status in RETRYABLE_HTTP or code == 'resource.conflict'
                    last_error = TezcheckError(code, resp.status, retryable, request_id)
                    if code == 'resource.conflict':
                        await asyncio.sleep(1)  # hujjat: Retry-After: 1
            except (aiohttp.ClientError, asyncio.TimeoutError):
                last_error = TezcheckError('network_error', None, True)
            if not last_error.retryable:
                break
        log.warning('Tezcheck %s %s xatosi: %s', method, path.split('/')[1] if '/' in path else path, last_error)
        raise last_error

    async def me(self) -> dict:
        return await self._request('GET', '/me', attempts=1)

    async def create_bill(self, *, amount_minor: int, title: str, external_reference: str,
                          idempotency_key: str) -> CreatedBill:
        body = {'amount_minor': amount_minor, 'title': title, 'external_reference': external_reference}
        data = await self._request('POST', '/bills', body, idempotency_key=idempotency_key)
        _require(isinstance(data, dict), 'data')
        return parse_created(data, external_reference, amount_minor)

    async def get_bill(self, bill_id: str) -> BillStatus:
        data = await self._request('POST', '/bills/' + quote(bill_id, safe=''))
        _require(isinstance(data, dict), 'data')
        return parse_status(data)
