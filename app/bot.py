"""Запуск бота: логирование, база, сборка роутеров, long polling."""

from __future__ import annotations

import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import BotCommand

from . import db, observability
from .config import settings
from .handlers import register

log = logging.getLogger(__name__)

# Синее меню команд в Telegram. Полный разбор справки — F-01; здесь список ровно
# из того, что хэндлеры уже умеют, плюс /privacy (E-01).
BOT_COMMANDS = [
    BotCommand(command="help", description="Справка"),
    BotCommand(command="city", description="Задать место, откуда спрашиваете"),
    BotCommand(command="me", description="Мои настройки и остаток"),
    BotCommand(command="history", description="Последние вопросы"),
    BotCommand(command="forget", description="Очистить историю"),
    BotCommand(command="privacy", description="Что хранится и кому передаётся"),
    BotCommand(command="terms", description="Что продаётся и как с возвратом"),
    BotCommand(command="paysupport", description="Поддержка по платежам"),
]

__all__ = ["register", "run"]


async def run() -> None:
    logging.basicConfig(
        level=getattr(logging, settings.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    observability.init()
    db.init()
    # Рестарт мог застать вопрос между reserve и commit — возвращаем такие кванты.
    db.release_stale()
    bot = Bot(settings.telegram_token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher(storage=MemoryStorage())
    dp.update.outer_middleware(observability.SentryMiddleware())
    register(dp)
    await bot.set_my_commands(BOT_COMMANDS)
    bot_info = await bot.get_me()
    log.info("Бот @%s запущен", bot_info.username)
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
