"""Постоянная клавиатура: держится между сообщениями и ведёт в нужные экраны (F-02)."""

from __future__ import annotations

from typing import Any

import pytest

from app import db, geo, llm, texts
from app.handlers.common import main_kb


@pytest.fixture(autouse=True)
def no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _boom(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("тест полез в сеть")

    monkeypatch.setattr(geo, "geocode", _boom)
    monkeypatch.setattr(llm, "interpret", _boom)


def _markup(sent) -> dict[str, Any] | None:
    return sent.data.get("reply_markup")


def test_keyboard_survives_between_messages() -> None:
    kb = main_kb()
    # one_time_keyboard оставил бы пользователя с пустым экраном после первого ответа.
    assert kb.is_persistent is True
    assert kb.one_time_keyboard is None


def test_keyboard_holds_all_four_buttons() -> None:
    labels = [b.text for row in main_kb().keyboard for b in row]
    assert labels == [texts.BTN_ASK, texts.BTN_BALANCE, texts.BTN_HISTORY, texts.BTN_HELP]


async def test_start_shows_the_keyboard(feed) -> None:
    sent = await feed("/start")
    assert _markup(sent[0]) is not None


async def test_ask_button_prompts_instead_of_asking(feed) -> None:
    # Подпись кнопки не должна уйти в прашну как вопрос.
    sent = await feed(texts.BTN_ASK)
    assert sent[0].text == texts.ASK_PROMPT
    assert db.history(777, 10) == []


async def test_balance_button_opens_the_balance_screen(feed) -> None:
    # Баланс и /me разошлись в F-05: первый про доступ, второй про настройки расчёта.
    from_button = await feed(texts.BTN_BALANCE)
    from_command = await feed("/me")
    assert from_button[0].text != from_command[0].text
    assert "Баланс" in from_button[0].text


async def test_history_button_matches_history(feed) -> None:
    from_button = await feed(texts.BTN_HISTORY)
    from_command = await feed("/history")
    assert from_button[0].text == from_command[0].text


async def test_help_button_shows_help(feed) -> None:
    sent = await feed(texts.BTN_HELP)
    assert sent[0].text == texts.HELP_MENU


async def test_button_press_drops_pending_city_input(feed, user_id) -> None:
    """Нажатие кнопки посреди ввода города означает, что ввод брошен."""
    await feed("/city")
    sent = await feed(texts.BTN_HELP)
    assert sent[0].text == texts.HELP_MENU
    # Город не установлен: текст кнопки геокодером не искали.
    assert db.get_user(user_id) is None or db.get_user(user_id)["lat"] is None


async def test_keyboard_returns_after_location_is_set(feed, user_id) -> None:
    from aiogram.types import Location

    sent = await feed(location=Location(latitude=56.5, longitude=84.97))
    assert _markup(sent[-1]) is not None
