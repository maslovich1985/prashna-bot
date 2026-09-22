"""Экран продажи при исчерпанных пробных (F-06)."""

from __future__ import annotations

from typing import Any

import pytest

from app import db, geo, llm, texts
from app.astro import validity
from app.constants import CONSULT_PRICE_MAX, CONSULT_PRICE_MIN, TRIAL_QUESTIONS
from app.handlers import billing
from app.handlers import prashna as prashna_handlers


@pytest.fixture(autouse=True)
def place_is_set(with_place) -> None:
    """Прашна-путь требует сохранённого места (F-07)."""


@pytest.fixture(autouse=True)
def valid_prashna(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        prashna_handlers, "_validity_of", lambda *args, **kwargs: validity.Verdict()
    )


@pytest.fixture(autouse=True)
def no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _boom(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("тест полез в сеть")

    monkeypatch.setattr(geo, "geocode", _boom)
    monkeypatch.setattr(llm, "interpret", _boom)
    monkeypatch.setattr(prashna_handlers.llm, "interpret", _boom)


@pytest.fixture
def spent(user_id: int) -> None:
    for _ in range(TRIAL_QUESTIONS):
        res, _ = db.reserve(user_id, cooldown=0)
        db.commit(res)


async def test_exhausted_trials_get_the_sales_screen(feed, spent, user_id) -> None:
    sent = await feed("Получу ли я эту работу?")
    assert sent[0].text == texts.sales_pitch(CONSULT_PRICE_MIN, CONSULT_PRICE_MAX)
    # Вопрос не посчитан: карта не считалась, квант не списан.
    assert db.history(user_id, 10) == []


async def test_sales_screen_leads_to_the_plans(feed, feed_callback, spent) -> None:
    await feed("Получу ли я эту работу?")
    sent = await feed_callback(billing.BUY_CALLBACK)
    assert any(texts.SALES_CLOSED in s.text for s in sent)


async def test_sales_screen_names_a_real_price(feed, spent) -> None:
    sent = await feed("Получу ли я эту работу?")
    assert f"{CONSULT_PRICE_MIN}–{CONSULT_PRICE_MAX}" in sent[0].text


def test_price_is_a_range_not_a_guess() -> None:
    # Цифры сняты с площадок и живут в constants; выдуманное сравнение
    # разваливается от первой же проверки.
    assert 0 < CONSULT_PRICE_MIN < CONSULT_PRICE_MAX


async def test_cooldown_is_not_a_sales_pitch(feed, user_id, monkeypatch) -> None:
    """Кулдаун и суточный лимит — обычные отказы: продавать тут нечего."""
    import dataclasses

    from app.config import settings

    monkeypatch.setattr(
        prashna_handlers, "settings", dataclasses.replace(settings, cooldown_seconds=3600)
    )
    res, _ = db.reserve(user_id, cooldown=0)
    db.commit(res)
    sent = await feed("Получу ли я эту работу?")
    assert texts.sales_pitch(CONSULT_PRICE_MIN, CONSULT_PRICE_MAX) not in [s.text for s in sent]
