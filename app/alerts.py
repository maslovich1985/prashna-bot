"""Алерты администратору в Telegram — отдельно от Sentry.

Протухший ключ Groq или падение расчёта карты означают, что сервис стоит целиком,
знать об этом надо в ту же минуту, а не при следующем заходе в дашборд.
Поэтому канал доставки тот же, что у бота, и лишних зависимостей не появляется.
"""

from __future__ import annotations

import logging
import time

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError

from .config import settings

log = logging.getLogger(__name__)

DEDUP_SECONDS = 1800  # один и тот же сбой не должен превращаться в поток сообщений

# Тип сбоя → момент последней отправки. Живёт в памяти процесса: после рестарта
# первый алерт уйдёт заново, и это правильно — рестарт мог быть из-за него же.
_last_sent: dict[str, float] = {}


def reset() -> None:
    """Сбрасывает дедупликацию. Нужен тестам, в боте не вызывается."""
    _last_sent.clear()


def _due(kind: str, now: float) -> bool:
    last = _last_sent.get(kind)
    return last is None or now - last >= DEDUP_SECONDS


async def notify(bot: Bot, kind: str, text: str) -> bool:
    """Шлёт алерт всем из `ADMIN_IDS`. Возвращает, ушло ли сообщение."""
    if not settings.admin_ids:
        log.warning("Алерт %s некому отправить: ADMIN_IDS пуст", kind)
        return False

    now = time.monotonic()
    if not _due(kind, now):
        log.debug("Алерт %s подавлен дедупликацией", kind)
        return False
    _last_sent[kind] = now

    delivered = False
    for admin_id in settings.admin_ids:
        try:
            await bot.send_message(admin_id, text)
            delivered = True
        except TelegramAPIError:
            # Сбой доставки алерта не должен ронять обработку вопроса.
            log.exception("Не удалось отправить алерт %s админу %s", kind, admin_id)
    return delivered
