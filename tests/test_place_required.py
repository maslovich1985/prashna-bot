"""Место обязательно и не подставляется молча (F-07)."""

from __future__ import annotations

from typing import Any

import pytest

from app import db, geo, llm, texts
from app.astro import validity
from app.config import settings
from app.handlers import prashna as prashna_handlers
from app.handlers.common import place_for

QUESTION = "Получу ли я эту работу?"


@pytest.fixture(autouse=True)
def valid_prashna(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        prashna_handlers, "_validity_of", lambda *args, **kwargs: validity.Verdict()
    )


@pytest.fixture(autouse=True)
def no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """Геокодер — под запретом, толкование подменено: карта считается настоящая,
    а в Groq тесты не ходят."""

    async def _boom(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("тест полез в сеть")

    async def _interpret(*args: Any, **kwargs: Any) -> llm.Answer:
        return llm.Answer(text="ТОЛКОВАНИЕ")

    monkeypatch.setattr(geo, "geocode", _boom)
    monkeypatch.setattr(llm, "interpret", _interpret)
    monkeypatch.setattr(prashna_handlers.llm, "interpret", _interpret)


async def test_question_without_place_is_not_calculated(feed, user_id) -> None:
    sent = await feed(QUESTION)
    assert sent[0].text == texts.NO_PLACE
    assert db.history(user_id, 10) == []


async def test_question_without_place_costs_nothing(feed, user_id) -> None:
    """Проверка идёт до reserve — иначе квант сгорал бы за ненайденное место."""
    before = db.entitlement_for(user_id).left
    await feed(QUESTION)
    assert db.entitlement_for(user_id).left == before


async def test_refusal_offers_the_city_list(feed) -> None:
    sent = await feed(QUESTION)
    kb = sent[0].data["reply_markup"]["inline_keyboard"]
    assert any("city:" in b["callback_data"] for row in kb for b in row)


async def test_default_city_never_reaches_the_user(feed, user_id) -> None:
    # DEFAULT_CITY остаётся для админских прогонов и astro-проверки из README.
    sent = await feed(QUESTION)
    assert settings.default_city not in sent[0].text
    assert await place_for(user_id) is None


async def test_stored_place_is_used_and_kept(feed, feed_callback, user_id) -> None:
    await feed("/city")
    await feed_callback("city:nnv")
    first = await feed(QUESTION)
    assert any("Нижний Новгород" in s.text for s in first)

    # Место переживает следующий вопрос: upsert в прашна-пути не перетирает его.
    stored = db.get_user(user_id)
    await feed(QUESTION)
    assert db.get_user(user_id)["place"] == stored["place"]
    assert db.get_user(user_id)["tz"] == stored["tz"]


async def test_short_chart_names_place_and_timezone(feed, feed_callback) -> None:
    await feed("/city")
    await feed_callback("city:kya")
    sent = await feed(QUESTION)
    joined = " ".join(s.text for s in sent)
    assert "Красноярск" in joined
    assert "Asia/Krasnoyarsk" in joined


async def test_me_says_place_is_missing(feed) -> None:
    sent = await feed("/me")
    assert "не задано" in sent[0].text
    assert settings.default_city not in sent[0].text


async def test_delete_me_warns_about_the_place(feed) -> None:
    sent = await feed("/delete_me")
    assert "место" in sent[0].text.lower()
