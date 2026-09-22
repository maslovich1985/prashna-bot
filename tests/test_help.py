"""Справка в три экрана и переходы между ними (F-03)."""

from __future__ import annotations

from typing import Any

import pytest

from app import geo, llm, texts
from app.handlers import basic


@pytest.fixture(autouse=True)
def no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _boom(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("тест полез в сеть")

    monkeypatch.setattr(geo, "geocode", _boom)
    monkeypatch.setattr(llm, "interpret", _boom)


def _buttons(sent) -> list[tuple[str, str]]:
    kb = sent.data["reply_markup"]["inline_keyboard"]
    return [(b["text"], b["callback_data"]) for row in kb for b in row]


async def test_help_opens_a_menu_of_three(feed) -> None:
    sent = await feed("/help")
    assert sent[0].text == texts.HELP_MENU
    assert [data for _, data in _buttons(sent[0])] == ["help:what", "help:how", "help:pay"]


@pytest.mark.parametrize(
    ("key", "screen"), [("what", texts.HELP_WHAT), ("how", texts.HELP_HOW), ("pay", texts.HELP_PAY)]
)
async def test_each_screen_opens(feed, feed_callback, key: str, screen: str) -> None:
    await feed("/help")
    sent = await feed_callback(f"help:{key}")
    edit = next(s for s in sent if s.method == "EditMessageText")
    assert edit.data["text"] == screen


async def test_back_returns_to_the_menu(feed, feed_callback) -> None:
    await feed("/help")
    await feed_callback("help:what")
    sent = await feed_callback("help:menu")
    edit = next(s for s in sent if s.method == "EditMessageText")
    assert edit.data["text"] == texts.HELP_MENU


async def test_unknown_screen_falls_back_to_the_menu(feed, feed_callback) -> None:
    # Кнопка из старого сообщения не должна оставлять пользователя в тупике.
    await feed("/help")
    sent = await feed_callback("help:нет-такого")
    edit = next(s for s in sent if s.method == "EditMessageText")
    assert edit.data["text"] == texts.HELP_MENU


def test_welcome_is_no_longer_a_wall() -> None:
    # DoD F-03: стена на 20 строк разъехалась по экранам справки.
    assert texts.WELCOME.count("\n") < 15
    assert "/help" in texts.WELCOME


def test_how_screen_explains_the_place_rule() -> None:
    # Место влияет на лагну — это главная причина «невнятных» ответов.
    assert "откуда вы спрашиваете" in texts.HELP_HOW
    assert "лагна" in texts.HELP_HOW


def test_how_screen_shows_good_and_bad_examples() -> None:
    assert texts.HELP_HOW.count("•") >= 8


def test_pay_screen_covers_stars_and_refunds() -> None:
    for marker in ("Stars", "/subscribe", "/terms", "/paysupport"):
        assert marker in texts.HELP_PAY


def test_disclaimer_survives_the_split() -> None:
    assert "рекомендательный характер" in texts.HELP_WHAT


def test_screens_are_reachable_from_the_map() -> None:
    assert set(basic.HELP_SCREENS) == {"what", "how", "pay"}
