"""Tezcheck webhook qabul qiluvchi HTTP server (Starlette + Uvicorn).

Server HTTP’da tinglaydi; ommaviy HTTPS’ni oldidagi reverse proxy (Caddy/Nginx) beradi — README’ga qarang.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import socket

import uvicorn
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from database import Database
from services.payments import PaymentService
from services.tezcheck import verify_webhook_signature

log = logging.getLogger(__name__)
MAX_BODY_BYTES = 64 * 1024


def create_app(settings, db: Database, payments: PaymentService) -> Starlette:
    async def tezcheck_webhook(request: Request) -> Response:
        raw = await request.body()
        if len(raw) > MAX_BODY_BYTES:
            return Response(status_code=413)
        headers = request.headers
        # Imzo XOM baytlar ustida tekshiriladi — JSON o‘qilishidan OLDIN.
        if not verify_webhook_signature(settings.tezcheck_webhook_secret, raw,
                                        headers.get('X-Checkout-Timestamp', ''),
                                        headers.get('X-Checkout-Delivery', ''),
                                        headers.get('X-Checkout-Signature', '')):
            log.warning('Webhook rad etildi: imzo yoki vaqt belgisi noto‘g‘ri')
            return Response(status_code=401)
        try:
            event = json.loads(raw)
        except (ValueError, UnicodeDecodeError):
            return Response(status_code=400)
        event_id = event.get('id') if isinstance(event, dict) else None
        if not isinstance(event_id, str) or not event_id or len(event_id) > 128:
            return Response(status_code=400)

        # Takrorlanishni tanadagi `id` bo‘yicha aniqlaymiz (hujjat talabi).
        previous = await db.register_webhook_event(event_id, str(event.get('type', '')),
                                                   headers.get('X-Checkout-Delivery', ''))
        if previous in ('processed', 'ignored'):
            return JSONResponse({'ok': True, 'duplicate': True})
        try:
            status, detail = await payments.process_webhook_event(event)
        except Exception as exc:
            log.exception('Webhook hodisasi qayta ishlanmadi: %s', event_id)
            await db.finish_webhook_event(event_id, 'failed', type(exc).__name__)
            return Response(status_code=500)  # Tezcheck keyinroq qayta yuboradi
        await db.finish_webhook_event(event_id, status, detail)
        log.info('Webhook %s: %s %s', event_id, status, detail or '')
        return JSONResponse({'ok': True})

    async def health(_: Request) -> Response:
        return JSONResponse({'status': 'ok'})

    return Starlette(routes=[
        Route(settings.webhook_path, tezcheck_webhook, methods=['POST']),
        Route('/health', health, methods=['GET']),
    ])


class _EmbeddedServer(uvicorn.Server):
    """Signallarni aiogram/asyncio boshqaradi; uvicorn ularni egallab olmasin."""

    @contextlib.contextmanager
    def capture_signals(self):
        yield


class WebhookServer:
    def __init__(self, app: Starlette, host: str, port: int):
        self.host, self.port = host, port
        self.server = _EmbeddedServer(uvicorn.Config(app, host=host, port=port, log_level='warning',
                                                     access_log=False, lifespan='off', proxy_headers=True))
        self.task: asyncio.Task | None = None

    def start(self) -> None:
        """Portni oldindan band qiladi: xato bo‘lsa tushunarli xabar chiqadi (uvicorn sys.exit qilmaydi)."""
        family = socket.AF_INET6 if ':' in self.host else socket.AF_INET
        sock = socket.socket(family, socket.SOCK_STREAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind((self.host, self.port))
        except OSError:
            sock.close()
            raise
        sock.set_inheritable(True)
        self.task = asyncio.create_task(self.server.serve(sockets=[sock]))

    async def stop(self) -> None:
        if self.task is None:
            return
        self.server.should_exit = True
        try:
            await asyncio.wait_for(self.task, timeout=10)
        except (asyncio.TimeoutError, asyncio.CancelledError):
            self.task.cancel()
        except BaseException:
            log.exception('Webhook serveri yopilishida xato')
