"""Потерянный после рестарта шаг «напишите город» (F-08)."""

from __future__ import annotations

from typing import Any

import pytest

from app import db, geo, llm, texts
from app.handlers import place
from app.handlers import prashna as prashna_handlers
from app.handlers.common import looks_like_city


@pytest.fixture(autouse=True)
def no_llm(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _boom(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("вопрос ушёл в расчёт, а не переспрос")

    monkeypatch.setattr(llm, "interpret", _boom)
    monkeypatch.setattr(prashna_handlers.llm, "interpret", _boom)


@pytest.mark.parametrize(
    "text", ["Нижний Новгород", "Санкт-Петербург", "Ростов-на-Дону", "Великий Новгород"]
)
def test_city_names_are_recognised(text: str) -> None:
    assert looks_like_city(text)


@pytest.mark.parametrize(
    "text",
    [
        "Получу ли я эту работу?",
        "Стоит ли переезжать в другой город",
        "вернётся ли долг",
        "Поправится ли отец после операции",
    ],
)
def test_questions_are_not_mistaken_for_cities(text: str) -> None:
    assert not looks_like_city(text)


async def test_lost_state_does_not_burn_a_quantum(feed, user_id, with_place) -> None:
    """DoD: состояние потеряно → сообщение из одного-двух слов переспрашивает."""
    before = db.entitlement_for(user_id).left
    sent = await feed("Нижний Новгород")
    assert texts.city_or_question("Нижний Новгород") == sent[0].text
    assert db.entitlement_for(user_id).left == before
    assert db.history(user_id, 10) == []


async def test_confirming_the_city_sets_the_place(
    feed, feed_callback, user_id, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def fake_geocode(query: str) -> geo.Place:
        assert query == "Нижний Новгород"
        return geo.Place("Нижний Новгород", 56.3269, 44.0059, "Europe/Moscow")

    monkeypatch.setattr(place.geo, "geocode", fake_geocode)
    await feed("Нижний Новгород")
    await feed_callback(place.PENDING_CITY)
    assert db.get_user(user_id)["place"] == "Нижний Новгород"


async def test_confirming_a_question_asks_to_resend(feed, feed_callback, user_id) -> None:
    await feed("Нижний Новгород")
    sent = await feed_callback(place.PENDING_QUESTION)
    assert any(texts.PENDING_SEND_AGAIN in s.text for s in sent)
    # Ничего не посчитано и не списано: карта строится на момент вопроса,
    # поэтому её придётся отправить заново.
    assert db.history(user_id, 10) == []


async def test_city_button_without_pending_asks_again(feed_callback) -> None:
    sent = await feed_callback(place.PENDING_CITY)
    assert any(texts.ASK_CITY in s.text for s in sent)
