"""Хэндлеры видны снаружи и зарегистрированы в нужном порядке."""

from __future__ import annotations

import pytest
from aiogram import Dispatcher

from app import bot

# aiogram проверяет хэндлеры по порядку регистрации: catch-all для прашны обязан
# стоять после всех команд, а fallback — последним. Порядок здесь не косметика,
# перестановка молча ломает маршрутизацию.
EXPECTED_ORDER = [
    "start",
    "help_cmd",
    "cancel",
    "city",
    "city_input",
    "location",
    "me",
    "history",
    "chart_cmd",
    "forget",
    "stats",
    "prashna",
    "fallback",
]


def _handler_names() -> list[str]:
    return [h.callback.__name__ for h in bot.router.message.handlers]


def test_handlers_are_importable() -> None:
    # Ради этого и делался A-12: пока хэндлеры были замыканиями внутри register(),
    # тесты не могли до них дотянуться.
    assert callable(bot.start)
    assert callable(bot.prashna)


def test_registration_order() -> None:
    assert _handler_names() == EXPECTED_ORDER


def test_register_attaches_router() -> None:
    dp = Dispatcher()
    bot.register(dp)
    assert bot.router in dp.sub_routers
    assert len(dp.sub_routers) == 1

    # router — синглтон модуля, поэтому register() рассчитан ровно на один
    # Dispatcher за процесс. aiogram ловит вторую попытку сам; тест фиксирует это,
    # чтобы будущие тесты хэндлеров строили свой Dispatcher, а не звали register().
    with pytest.raises(RuntimeError, match="already attached"):
        bot.register(Dispatcher())
