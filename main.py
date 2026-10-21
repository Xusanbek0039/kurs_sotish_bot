"""Kurs sotish boti. Ishga tushirish: python main.py

Bitta jarayonda bir vaqtda:
  * Telegram bot (long polling — ommaviy server/HTTPS manzil kerak emas)
  * ochiq to‘lovlarni Tezcheck API orqali davriy tekshiruvchi va havolalarni qayta yuboruvchi fon vazifa
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
import sys

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.exceptions import TelegramAPIError, TelegramUnauthorizedError
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import BotCommand, BotCommandScopeChat, BotCommandScopeDefault

import handlers
from config import ConfigError, Settings, load_settings
from database import Database
from services.payments import PaymentService
from services.tezcheck import TezcheckClient, TezcheckError
from services.ui import EmojiFallbackMiddleware, apply_appearance

log = logging.getLogger('bot')


class RedactSecrets(logging.Filter):
    """Bot tokeni va Tezcheck API kaliti hech qachon logga tushmasin."""

    def __init__(self, secrets: list[str]):
        super().__init__()
        self.secrets = [s for s in secrets if len(s) >= 6]

    def _clean(self, text: str) -> str:
        for secret in self.secrets:
            text = text.replace(secret, '***')
        return text

    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        cleaned = self._clean(message)
        if record.exc_info and not record.exc_text:
            record.exc_text = logging.Formatter().formatException(record.exc_info)
        if record.exc_text:
            record.exc_text = self._clean(record.exc_text)
        if cleaned != message or record.args:
            record.msg, record.args = cleaned, None
        return True


def setup_logging(secrets: list[str] | None = None) -> None:
    # Windows konsolida o‘zbek/rus harflari buzilmasligi uchun.
    with contextlib.suppress(AttributeError, ValueError):
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter('%(asctime)s %(levelname)-7s %(name)s: %(message)s', '%Y-%m-%d %H:%M:%S'))
    if secrets:
        handler.addFilter(RedactSecrets(secrets))
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(logging.INFO)
    for noisy in ('aiogram.event', 'aiosqlite'):
        logging.getLogger(noisy).setLevel(logging.WARNING)


async def set_commands(bot: Bot, settings: Settings) -> None:
    # Menyu buyruqlari tavsifida Telegram faqat oddiy matn/emoji qabul qiladi (custom emoji ishlamaydi).
    common = {
        'uz': [BotCommand(command='start', description='🏠 Bosh sahifa'),
               BotCommand(command='courses', description='📚 Kurslar'),
               BotCommand(command='mycourses', description='🎓 Kurslarim'),
               BotCommand(command='language', description='🌐 Til / Язык'),
               BotCommand(command='help', description='💬 Yordam')],
        'ru': [BotCommand(command='start', description='🏠 Главная'),
               BotCommand(command='courses', description='📚 Курсы'),
               BotCommand(command='mycourses', description='🎓 Мои курсы'),
               BotCommand(command='language', description='🌐 Til / Язык'),
               BotCommand(command='help', description='💬 Помощь')],
    }
    admin_extra = {'uz': BotCommand(command='admin', description='⚙️ Admin panel'),
                   'ru': BotCommand(command='admin', description='⚙️ Админ-панель')}
    with contextlib.suppress(TelegramAPIError):
        await bot.set_my_commands(common[settings.default_language], scope=BotCommandScopeDefault())
        await bot.set_my_commands(common['ru'], scope=BotCommandScopeDefault(), language_code='ru')
        await bot.set_my_commands(common['uz'], scope=BotCommandScopeDefault(), language_code='uz')
    for admin_id in settings.admin_ids:
        # Admin hali botni ochmagan bo‘lsa, Telegram xato qaytaradi — bu normal holat.
        with contextlib.suppress(TelegramAPIError):
            lang = settings.default_language
            await bot.set_my_commands(common[lang] + [admin_extra[lang]], scope=BotCommandScopeChat(chat_id=admin_id))


async def check_tezcheck(api: TezcheckClient) -> None:
    """GET /me — pul harakatisiz: kalit va kassa kodi bir-biriga mosligini tekshiradi."""
    try:
        me = await api.me()
    except TezcheckError as exc:
        hints = {'auth.unauthenticated': 'TEZCHECK_API_KEY noto‘g‘ri yoki bekor qilingan.',
                 'resource.not_found': 'TEZCHECK_CASH_DESK_CODE topilmadi (kassa kodi, ID emas).',
                 'auth.permission_denied': 'Kalit boshqa kassaga biriktirilgan.',
                 'auth.forbidden': 'Server IP manzili kassaning IP ro‘yxatida yo‘q.',
                 'request.signature_invalid': 'Kalit faqat imzolangan so‘rovlarni qabul qiladi — kabinetda o‘chiring.',
                 'network_error': 'Tezcheck bilan aloqa yo‘q (internet?).'}
        log.error('Tezcheck tekshiruvi: %s — %s Bot ishlayveradi, lekin to‘lovlar ishlamasligi mumkin.',
                  exc.code, hints.get(exc.code, ''))
        return
    desk = me.get('cash_desk') if isinstance(me, dict) else None
    if not isinstance(desk, dict):
        log.error('Tezcheck: kassa aniqlanmadi (cash_desk=null). TEZCHECK_CASH_DESK_CODE ni tekshiring.')
        return
    log.info('Tezcheck ulanishi: OK (kassa: %s, valyuta: %s)', desk.get('name'), desk.get('currency'))


async def run(settings: Settings) -> None:
    db = Database(settings.database_path)
    await db.connect()
    log.info('Ma’lumotlar bazasi: %s', settings.database_path)
    apply_appearance(settings, await db.app_settings())   # admin bot ichidan qo‘ygan stiker/emoji’lar
    reset = await db.reset_stuck_deliveries()
    if reset:
        log.info('Yakunlanmagan %s ta havola yuborish qayta navbatga qo‘yildi', reset)

    bot = Bot(settings.bot_token, default=DefaultBotProperties(parse_mode='HTML', link_preview_is_disabled=True))
    bot.session.middleware(EmojiFallbackMiddleware(settings.emoji_map))
    api = TezcheckClient(settings.tezcheck_base_url, settings.tezcheck_api_key, settings.tezcheck_cash_desk_code)
    payments = PaymentService(db, api, bot, settings)
    stop = asyncio.Event()
    poller: asyncio.Task | None = None
    try:
        try:
            me = await bot.get_me()
        except TelegramUnauthorizedError:
            raise ConfigError('BOT_TOKEN noto‘g‘ri yoki bekor qilingan. @BotFather’dan yangi token oling.') from None
        log.info('Bot: @%s (id=%s)', me.username, me.id)
        await check_tezcheck(api)

        dp = Dispatcher(storage=MemoryStorage(), settings=settings, db=db, payments=payments)
        handlers.setup(dp)
        await set_commands(bot, settings)
        # Telegram webhook o‘rnatilgan bo‘lsa, polling ishlamaydi — uni olib tashlaymiz (yangilanishlar saqlanadi).
        await bot.delete_webhook(drop_pending_updates=False)

        log.info('To‘lovlar Tezcheck API orqali tasdiqlanadi: “To‘lovni tekshirish” tugmasi va har %s soniyadagi '
                 'fon tekshiruvi.', settings.payment_check_interval)
        poller = asyncio.create_task(payments.run_poller(stop))
        log.info('Bot ishga tushdi. To‘xtatish: Ctrl+C')
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types(), close_bot_session=False)
    finally:
        log.info('To‘xtatilmoqda…')
        stop.set()
        if poller:
            poller.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await poller
        await payments.shutdown()
        await api.close()
        await bot.session.close()
        await db.close()
        log.info('Bot to‘xtatildi.')


def main() -> int:
    setup_logging()
    if sys.version_info < (3, 11):
        log.error('Python 3.11 yoki yangiroq versiya kerak (hozir: %s).', sys.version.split()[0])
        return 1
    try:
        settings = load_settings()
    except ConfigError as exc:
        log.error('Sozlama xatosi: %s', exc)
        log.error('.env.example faylidan nusxa olib .env yarating va qiymatlarni to‘ldiring.')
        return 1
    setup_logging(settings.secrets())
    try:
        asyncio.run(run(settings))
    except KeyboardInterrupt:
        pass
    except ConfigError as exc:
        log.error('Sozlama xatosi: %s', exc)
        return 1
    except TelegramAPIError as exc:
        log.error('Telegram bilan bog‘lanib bo‘lmadi: %s', type(exc).__name__)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
