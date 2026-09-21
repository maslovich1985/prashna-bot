"""Хэндлеры видны снаружи и подключаются в нужном порядке."""

from __future__ import annotations

from aiogram import Dispatcher

from app import bot
from app.handlers import ROUTERS, register

# aiogram проверяет хэндлеры по порядку регистрации. Порядок здесь не косметика:
# catch-all прашны перехватит и команды, и ввод города в FSM, если окажется выше.
EXPECTED_ORDER = [
    # basic
    "start",
    "help_cmd",
    "privacy",
    "cancel",
    "me",
    "history",
    "chart_cmd",
    "forget",
    "stats",
    # place
    "city",
    "city_input",
    "location",
    # prashna — последним
    "prashna",
    "fallback",
]


def _handler_names() -> list[str]:
    return [h.callback.__name__ for r in ROUTERS for h in r.message.handlers]


def test_handlers_are_importable() -> None:
    from app.handlers import basic, place
    from app.handlers import prashna as prashna_handlers

    assert callable(basic.start)
    assert callable(place.city)
    assert callable(prashna_handlers.prashna)


def test_registration_order() -> None:
    assert _handler_names() == EXPECTED_ORDER


def test_prashna_router_is_last() -> None:
    assert ROUTERS[-1].name == "prashna"


def test_register_attaches_all_routers() -> None:
    dp = Dispatcher()
    register(dp)
    assert [r.name for r in dp.sub_routers] == ["basic", "place", "prashna"]


def test_bot_module_reexports_register() -> None:
    # run() собирает диспетчер через этот же register
    assert bot.register is register
