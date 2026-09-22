"""Алерты админу: дедупликация по типу сбоя и доставка всем из ADMIN_IDS."""

from __future__ import annotations

import dataclasses
from typing import Any

import pytest
from aiogram.exceptions import TelegramBadRequest

from app import alerts, db, llm
from app.handlers import prashna as prashna_handlers


@pytest.fixture(autouse=True)
def clean_dedup() -> None:
    alerts.reset()


@pytest.fixture(autouse=True)
def place_is_set(with_place) -> None:
    """Прашна-путь требует сохранённого места (F-07)."""


@pytest.fixture
def admins(monkeypatch: pytest.MonkeyPatch) -> tuple[int, ...]:
    ids = (901, 902)
    monkeypatch.setattr(alerts, "settings", dataclasses.replace(alerts.settings, admin_ids=ids))
    return ids


async def test_alert_goes_to_every_admin(bot, session, admins) -> None:
    assert await alerts.notify(bot, "llm_auth", "тревога") is True
    assert [s.data["chat_id"] for s in session.sent] == list(admins)


async def test_same_kind_is_deduplicated(bot, session, admins) -> None:
    await alerts.notify(bot, "llm_auth", "тревога")
    assert await alerts.notify(bot, "llm_auth", "снова") is False
    assert len(session.sent) == len(admins)


async def test_other_kind_is_not_deduplicated(bot, session, admins) -> None:
    await alerts.notify(bot, "llm_auth", "тревога")
    assert await alerts.notify(bot, "chart_failed", "другая") is True


async def test_dedup_window_expires(bot, session, admins, monkeypatch: pytest.MonkeyPatch) -> None:
    clock = {"t": 0.0}
    monkeypatch.setattr(alerts.time, "monotonic", lambda: clock["t"])

    await alerts.notify(bot, "llm_auth", "тревога")
    clock["t"] = alerts.DEDUP_SECONDS - 1
    assert await alerts.notify(bot, "llm_auth", "ещё рано") is False
    clock["t"] = alerts.DEDUP_SECONDS + 1
    assert await alerts.notify(bot, "llm_auth", "прошло полчаса") is True


async def test_without_admins_nothing_is_sent(
    bot, session, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(alerts, "settings", dataclasses.replace(alerts.settings, admin_ids=()))
    assert await alerts.notify(bot, "llm_auth", "тревога") is False
    assert session.sent == []


async def test_delivery_failure_does_not_raise(bot, session, admins, monkeypatch) -> None:
    async def refuse(*args: Any, **kwargs: Any) -> Any:
        raise TelegramBadRequest(method=None, message="chat not found")  # type: ignore[arg-type]

    monkeypatch.setattr(session, "make_request", refuse)
    assert await alerts.notify(bot, "llm_auth", "тревога") is False


async def test_groq_auth_error_alerts_admin(feed, session, admins, monkeypatch) -> None:
    async def failing(*args: Any, **kwargs: Any) -> llm.Answer:
        raise llm.LLMAuthError("HTTP 401")

    monkeypatch.setattr(prashna_handlers.llm, "interpret", failing)
    await feed("Получу ли я эту работу в этом году?")
    assert any("Groq отверг ключ" in t for t in session.texts)


async def test_chart_failure_alerts_admin(feed, session, admins, monkeypatch) -> None:
    def boom(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError("расчёт упал")

    monkeypatch.setattr(prashna_handlers, "build_chart", boom)
    await feed("Получу ли я эту работу в этом году?")
    assert any("Не рассчиталась карта" in t for t in session.texts)


async def test_two_failures_give_one_alert(feed, session, admins, monkeypatch) -> None:
    async def failing(*args: Any, **kwargs: Any) -> llm.Answer:
        raise llm.LLMAuthError("HTTP 401")

    monkeypatch.setattr(prashna_handlers.llm, "interpret", failing)
    # Пробных хватит на два вопроса, и оба вернут квант — отказ не списывается.
    await feed("Получу ли я эту работу в этом году?")
    await feed("Получу ли я эту работу в этом году?")
    alerts_sent = [t for t in session.texts if "Groq отверг ключ" in t]
    assert len(alerts_sent) == len(admins)
    assert db.entitlement_for(777).left == 2
