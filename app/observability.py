"""Sentry: инициализация и мидлварь с контекстом события.

Текст вопроса в Sentry не уходит: это данные о здоровье, деньгах и отношениях.
Поэтому `send_default_pii=False`, а из апдейта берём только идентификатор
пользователя и имя команды.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any

import sentry_sdk
from aiogram import BaseMiddleware
from aiogram.types import Message, TelegramObject, Update

from .config import settings

log = logging.getLogger(__name__)

# Событие без команды: свободный текст — это прашна, всё остальное неизвестно.
PRASHNA_EVENT = "prashna"
UNKNOWN_EVENT = "—"


def init() -> bool:
    """Поднимает Sentry, если задан DSN. Возвращает, включён ли он."""
    if not settings.sentry_dsn:
        log.info("SENTRY_DSN не задан — Sentry выключен")
        return False

    sentry_sdk.init(
        dsn=settings.sentry_dsn,
        environment=settings.sentry_env,
        traces_sample_rate=0.0,
        send_default_pii=False,
    )
    log.info("Sentry включён, окружение %s", settings.sentry_env)
    return True


def event_name(message: Message | None) -> str:
    """Имя команды (`/city`) либо тип события — но не текст пользователя."""
    if message is None:
        return UNKNOWN_EVENT
    text = message.text or message.caption or ""
    if text.startswith("/"):
        # «/city Томск» и «/city@prashna_bot» → «/city»
        return text.split(maxsplit=1)[0].split("@", 1)[0]
    if text:
        return PRASHNA_EVENT
    if message.location:
        return "location"
    return UNKNOWN_EVENT


class SentryMiddleware(BaseMiddleware):
    """Вешает на событие теги `user_id` и `command`."""

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        message = event.message if isinstance(event, Update) else None
        user = data.get("event_from_user")
        # isolation_scope: теги живут в пределах одного апдейта и не текут в соседний
        with sentry_sdk.isolation_scope() as scope:
            scope.set_tag("user_id", user.id if user else None)
            scope.set_tag("command", event_name(message))
            return await handler(event, data)


def tag_llm_failure(source: str, kind: str) -> None:
    """Помечает сбой LLM в Sentry. Без DSN это no-op: счётчики и логи работают и так."""
    if not settings.sentry_dsn:
        return
    sentry_sdk.set_tag("llm_source", source)
    sentry_sdk.set_tag("llm_failure", kind)
