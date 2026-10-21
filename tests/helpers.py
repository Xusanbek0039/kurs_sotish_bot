"""Testlar uchun soxta (tarmoqsiz) Telegram va Tezcheck. Jonli kodda ishlatilmaydi."""
from __future__ import annotations

import tempfile
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

from aiogram.client.session.base import BaseSession
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.methods import (AnswerCallbackQuery, CreateChatInviteLink, EditMessageText, GetChat, GetChatMember,
                             GetMe, SendDocument, SendMessage, SendPhoto)
from aiogram.types import Chat, Message, User

from config import Settings
from database import Database
from services.tezcheck import BillStatus, CreatedBill, TezcheckError

ADMIN_ID = 1001
USER_ID = 2002
CHANNEL_ID = -1001234567890


def make_settings(db_path: Path, **overrides) -> Settings:
    values = dict(bot_token='123456:' + 'A' * 35, admin_ids=frozenset({ADMIN_ID}), tezcheck_api_key='aps_test_key',
                  tezcheck_cash_desk_code='cdk_test',
                  database_path=db_path, payment_check_interval=60)
    values.update(overrides)
    return Settings(**values)


async def make_db() -> tuple[Database, tempfile.TemporaryDirectory]:
    tmp = tempfile.TemporaryDirectory()
    db = Database(Path(tmp.name) / 'data' / 'test.sqlite3')
    await db.connect()
    return db, tmp


async def seed(db: Database, price: int = 150000) -> int:
    await db.touch_user(USER_ID, 'buyer', 'Ali')
    await db.set_language(USER_ID, 'uz')
    await db.touch_user(ADMIN_ID, 'boss', 'Admin')
    return await db.add_course(ADMIN_ID, {'name_uz': 'Python kursi', 'name_ru': 'Курс Python',
                                          'description_uz': 'Noldan boshlab Python', 'description_ru': 'Python с нуля',
                                          'price_uzs': price, 'channel_chat_id': CHANNEL_ID})


class FakeTezcheck:
    """Tezcheck API o‘rnini bosuvchi soxta mijoz. Holatlar test ichidan boshqariladi."""

    def __init__(self):
        self.created: list[dict] = []
        self.bills: dict[str, dict] = {}
        self.fail_create: TezcheckError | None = None
        self.fail_get: TezcheckError | None = None

    async def create_bill(self, *, amount_minor, title, external_reference, idempotency_key):
        self.created.append({'amount_minor': amount_minor, 'title': title, 'ref': external_reference,
                             'key': idempotency_key})
        if self.fail_create:
            raise self.fail_create
        # Idempotency-Key: bir xil kalit — bir xil hisob (Tezcheck xatti-harakatiga o‘xshash).
        for bill_id, bill in self.bills.items():
            if bill['key'] == idempotency_key:
                return CreatedBill(bill_id, 'https://pay.tezcheck.uz/pay/' + bill_id)
        bill_id = f'link_{len(self.bills) + 1}'
        self.bills[bill_id] = {'key': idempotency_key, 'ref': external_reference, 'amount': amount_minor,
                               'paid': False, 'livemode': True, 'environment': None, 'state': 'active',
                               'payment': None}
        return CreatedBill(bill_id, 'https://pay.tezcheck.uz/pay/' + bill_id)

    def pay(self, bill_id: str, environment: str = 'live', amount: int | None = None):
        bill = self.bills[bill_id]
        live = environment == 'live'
        bill.update(paid=True, livemode=live, environment=environment, state='exhausted')
        bill['payment'] = {'id': 'pay_' + bill_id, 'state': 'succeeded', 'amount': amount or bill['amount'],
                           'livemode': live, 'environment': environment, 'refunded': 0}

    async def get_bill(self, bill_id):
        if self.fail_get:
            raise self.fail_get
        b = self.bills[bill_id]
        p = b['payment'] or {}
        return BillStatus(bill_id=bill_id, state=b['state'], paid=b['paid'], livemode=b['livemode'],
                          environment=b['environment'], amount_minor=b['amount'], external_reference=b['ref'],
                          unavailability_reason=None, payment_id=p.get('id'), payment_state=p.get('state'),
                          payment_amount_minor=p.get('amount'), payment_livemode=p.get('livemode'),
                          payment_environment=p.get('environment'), refunded_amount_minor=p.get('refunded'))


class FakeBot:
    """PaymentService uchun soxta bot: Telegram chaqiruvlarini yozib boradi."""

    def __init__(self):
        self.invites: list[dict] = []
        self.revoked: list[str] = []
        self.messages: list[tuple[int, str, object]] = []
        self.fail_invite = False
        self.blocked_users: set[int] = set()
        self.members: set[int] = set()

    async def create_chat_invite_link(self, chat_id, name=None, expire_date=None, member_limit=None, **_):
        if self.fail_invite:
            raise TelegramBadRequest(method=None, message='Bad Request: not enough rights to manage chat invite link')
        link = f'https://t.me/+invite{len(self.invites) + 1}'
        self.invites.append({'chat_id': chat_id, 'name': name, 'expire_date': expire_date,
                             'member_limit': member_limit, 'link': link})
        return SimpleNamespace(invite_link=link)

    async def revoke_chat_invite_link(self, chat_id, invite_link):
        self.revoked.append(invite_link)

    async def send_message(self, chat_id, text, reply_markup=None, **_):
        if chat_id in self.blocked_users:
            raise TelegramForbiddenError(method=None, message='Forbidden: bot was blocked by the user')
        self.messages.append((chat_id, text, reply_markup))

    async def send_sticker(self, chat_id, sticker):
        pass

    async def send_photo(self, chat_id, photo, caption=None, reply_markup=None, **_):
        self.messages.append((chat_id, caption, reply_markup))

    async def send_document(self, chat_id, document, caption=None, reply_markup=None, **_):
        self.messages.append((chat_id, caption, reply_markup))

    async def get_chat_member(self, chat_id, user_id):
        return SimpleNamespace(status='member' if user_id in self.members else 'left')

    def links_sent_to(self, user_id: int) -> list[str]:
        out = []
        for chat_id, _, markup in self.messages:
            if chat_id == user_id and markup is not None:
                out += [b.url for row in markup.inline_keyboard for b in row if b.url and '/+' in b.url]
        return out


class RecordingSession(BaseSession):
    """aiogram uchun tarmoqsiz sessiya: dispatcher orqali handlerlarni to‘liq sinash uchun."""

    def __init__(self):
        super().__init__()
        self.requests: list = []

    async def close(self):
        pass

    async def stream_content(self, *args, **kwargs):  # pragma: no cover
        yield b''

    async def make_request(self, bot, method, timeout=None):
        self.requests.append(method)
        if isinstance(method, GetMe):
            return User(id=999, is_bot=True, first_name='Bot', username='kurs_test_bot')
        if isinstance(method, (SendMessage, EditMessageText, SendDocument, SendPhoto)):
            return Message(message_id=len(self.requests), date=datetime.now(timezone.utc),
                           chat=Chat(id=int(method.chat_id or 0), type='private'),
                           text=getattr(method, 'text', None) or '')
        if isinstance(method, CreateChatInviteLink):
            return SimpleNamespace(invite_link=f'https://t.me/+invite{len(self.requests)}')
        if isinstance(method, GetChat):
            return SimpleNamespace(id=method.chat_id, type='channel')
        if isinstance(method, GetChatMember):
            # Bot kanalda admin va taklif huquqiga ega (kanal tekshiruvi o‘tadi).
            return SimpleNamespace(status='administrator', can_invite_users=True)
        return True

    def texts(self) -> list[str]:
        return [m.text for m in self.requests if isinstance(m, (SendMessage, EditMessageText))]

    def callback_answers(self) -> list[AnswerCallbackQuery]:
        return [m for m in self.requests if isinstance(m, AnswerCallbackQuery)]
