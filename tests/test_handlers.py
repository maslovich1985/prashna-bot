"""Хэндлеры целиком: настоящий Dispatcher, стаб сессии вместо Bot API.

Сеть не дёргается: геокодер и Groq замоканы, всё остальное — реальный код и SQLite.
"""

from __future__ import annotations

import dataclasses
from typing import Any

import pytest
from aiogram.types import Location

from app import db, geo, llm, texts
from app.config import settings
from app.handlers import basic
from app.handlers import prashna as prashna_handlers

USER_ID = 777  # совпадает с отправителем из фикстуры feed

TOMSK = geo.Place("Томск", 56.5, 84.97, "Asia/Tomsk")


@pytest.fixture
def no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """Страховка: любой поход в сеть из-под теста — ошибка, а не молчаливый запрос."""

    async def _boom(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("тест полез в сеть")

    monkeypatch.setattr(geo, "geocode", _boom)
    monkeypatch.setattr(llm, "interpret", _boom)
    monkeypatch.setattr(prashna_handlers.llm, "interpret", _boom)


async def test_start_greets_and_registers_user(feed, no_network) -> None:
    sent = await feed("/start")
    assert texts.WELCOME in sent[0].text
    assert sent[0].data.get("reply_markup") is not None
    assert db.get_user(USER_ID) is not None


async def test_help_returns_welcome(feed, no_network) -> None:
    sent = await feed("/help")
    assert [s.text for s in sent] == [texts.WELCOME]


async def test_city_with_argument_sets_place(feed, monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_geocode(query: str) -> geo.Place:
        assert query == "Томск"
        return TOMSK

    monkeypatch.setattr(geo, "geocode", fake_geocode)
    sent = await feed("/city Томск")
    assert "Томск" in sent[-1].text
    user = db.get_user(USER_ID)
    assert (user["lat"], user["lon"], user["tz"]) == (TOMSK.lat, TOMSK.lon, TOMSK.tz)


async def test_city_not_found_keeps_place_unset(feed, monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_geocode(query: str) -> None:
        return None

    monkeypatch.setattr(geo, "geocode", fake_geocode)
    sent = await feed("/city Блаблабла")
    assert sent[-1].text == texts.CITY_NOT_FOUND
    assert db.get_user(USER_ID) is None


async def test_city_without_argument_asks_and_takes_next_message(
    feed, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def fake_geocode(query: str) -> geo.Place:
        return TOMSK

    monkeypatch.setattr(geo, "geocode", fake_geocode)
    asked = await feed("/city")
    assert asked[-1].text == texts.ASK_CITY
    # FSM активен: следующий текст — город, а не вопрос для прашны
    answered = await feed("Томск")
    assert "Томск" in answered[-1].text
    assert db.get_user(USER_ID)["place"] == "Томск"


async def test_location_sets_place(feed, no_network) -> None:
    sent = await feed(location=Location(latitude=56.5, longitude=84.97))
    assert "56.5000" in sent[-1].text
    assert db.get_user(USER_ID)["tz"] == "Asia/Tomsk"


async def test_me_shows_default_place_and_limit(feed, no_network) -> None:
    sent = await feed("/me")
    assert settings.default_city in sent[-1].text
    assert str(settings.daily_limit) in sent[-1].text


async def test_history_empty(feed, no_network) -> None:
    sent = await feed("/history")
    assert [s.text for s in sent] == [texts.HISTORY_EMPTY]


async def test_stats_for_non_admin_reveals_nothing(
    feed, no_network, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(basic, "settings", dataclasses.replace(settings, admin_ids=(999,)))
    sent = await feed("/stats")
    assert all("users" not in s.text for s in sent)


async def test_unknown_non_text_message_falls_back(feed, no_network) -> None:
    sent = await feed()
    assert [s.text for s in sent] == [texts.FALLBACK]


async def test_short_question_rejected_without_llm(feed, no_network) -> None:
    sent = await feed("а что")
    assert [s.text for s in sent] == [texts.QUESTION_TOO_SHORT]


async def test_long_question_rejected_without_llm(feed, no_network) -> None:
    sent = await feed("а" * 501)
    assert [s.text for s in sent] == [texts.QUESTION_TOO_LONG]


async def test_prashna_sends_chart_before_llm_answer(
    feed, session, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen_before_llm: list[str] = []

    async def fake_interpret(*args: Any, **kwargs: Any) -> str:
        seen_before_llm.extend(session.texts)
        return "Ответ LLM: да."

    monkeypatch.setattr(prashna_handlers.llm, "interpret", fake_interpret)

    sent = await feed("Получу ли я эту работу в этом году?")
    # к моменту вызова LLM карта уже ушла пользователю
    assert seen_before_llm and "Прашна принята" in seen_before_llm[0]

    texts_sent = [s.text for s in sent if s.text]
    assert "Прашна принята" in texts_sent[0]
    assert "Ответ LLM: да." in texts_sent[1]
    assert texts_sent[-1].startswith("Полная карта:")

    rows = db.history(USER_ID, 10)
    assert len(rows) == 1
    assert db.get_prashna(USER_ID, rows[0]["id"])["answer"] == "Ответ LLM: да."


async def test_prashna_keeps_chart_when_llm_fails(feed, monkeypatch: pytest.MonkeyPatch) -> None:
    async def failing(*args: Any, **kwargs: Any) -> str:
        raise llm.LLMError("Groq недоступен")

    monkeypatch.setattr(prashna_handlers.llm, "interpret", failing)
    sent = await feed("Получу ли я эту работу в этом году?")
    texts_sent = [s.text for s in sent if s.text]
    assert "Прашна принята" in texts_sent[0]
    assert texts_sent[-1] == texts.LLM_UNAVAILABLE
    assert db.history(USER_ID, 10) == []


async def test_chart_command_returns_saved_prashna(feed, no_network) -> None:
    pid = db.save_prashna(USER_ID, "Вопрос?", 10, "Томск", "ТЕКСТ КАРТЫ", "ТОЛКОВАНИЕ")
    sent = await feed(f"/chart {pid}")
    assert sent[-1].method == "SendDocument"
    assert f"Прашна #{pid}" in sent[-1].text


async def test_chart_command_rejects_foreign_id(feed, no_network) -> None:
    pid = db.save_prashna(USER_ID + 1, "Чужой вопрос?", 10, "Томск", "КАРТА", "ОТВЕТ")
    sent = await feed(f"/chart {pid}")
    assert [s.text for s in sent] == [texts.CHART_NOT_FOUND]


async def test_forget_clears_history(feed, no_network) -> None:
    db.save_prashna(USER_ID, "Вопрос?", 10, "Томск", "КАРТА", "ОТВЕТ")
    sent = await feed("/forget")
    assert sent[-1].text == texts.forgotten(1)
    assert db.history(USER_ID, 10) == []
