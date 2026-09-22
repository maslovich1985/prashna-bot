"""Быстрый выбор города кнопкой: без единого запроса в Nominatim (F-04)."""

from __future__ import annotations

from typing import Any

import pytest

from app import db, geo, llm, texts
from app.constants import CITIES
from app.handlers import place

EXPECTED = [
    "Москва",
    "Санкт-Петербург",
    "Новосибирск",
    "Екатеринбург",
    "Казань",
    "Нижний Новгород",
    "Челябинск",
    "Красноярск",
    "Самара",
    "Уфа",
]


@pytest.fixture(autouse=True)
def no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """DoD F-04: выбор города кнопкой не делает ни одного HTTP-запроса."""

    async def _boom(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("выбор города полез в геокодер")

    monkeypatch.setattr(geo, "geocode", _boom)
    monkeypatch.setattr(place.geo, "geocode", _boom)
    monkeypatch.setattr(llm, "interpret", _boom)


def _buttons(sent) -> list[tuple[str, str]]:
    kb = sent.data["reply_markup"]["inline_keyboard"]
    return [(b["text"], b["callback_data"]) for row in kb for b in row]


def test_ten_cities_are_constants() -> None:
    assert [c.name for c in CITIES.values()] == EXPECTED


def test_every_city_has_coordinates_and_tz() -> None:
    for key, city in CITIES.items():
        assert -90 <= city.lat <= 90 and -180 <= city.lon <= 180, key
        assert "/" in city.tz, key


def test_city_timezones_match_the_coordinates() -> None:
    # Зашитый tz — источник ошибки, которую видно только в карте: сверяем с оффлайн-базой.
    for key, city in CITIES.items():
        assert geo.tz_for(city.lat, city.lon) == city.tz, key


async def test_city_without_argument_offers_the_list(feed) -> None:
    sent = await feed("/city")
    picker = next(s for s in sent if s.data.get("reply_markup", {}).get("inline_keyboard"))
    labels = [text for text, _ in _buttons(picker)]
    assert labels[:-1] == EXPECTED
    assert labels[-1] == texts.CITY_OTHER_BUTTON


async def test_geolocation_button_stays_alongside(feed) -> None:
    sent = await feed("/city")
    assert any("keyboard" in (s.data.get("reply_markup") or {}) for s in sent)


async def test_choosing_a_city_sets_the_place(feed, feed_callback, user_id) -> None:
    await feed("/city")
    await feed_callback("city:nnv")
    user = db.get_user(user_id)
    assert user["place"] == "Нижний Новгород"
    assert user["tz"] == "Europe/Moscow"
    assert round(user["lat"], 4) == CITIES["nnv"].lat


async def test_other_city_falls_back_to_typing(feed, feed_callback, user_id) -> None:
    await feed("/city")
    sent = await feed_callback("city:other")
    assert any(texts.ASK_CITY in s.text for s in sent)
    assert db.get_user(user_id) is None


async def test_typing_a_city_still_works(feed, monkeypatch: pytest.MonkeyPatch) -> None:
    """Кнопки не отменяют ввод руками: FSM после /city по-прежнему ждёт название."""

    async def fake_geocode(query: str):
        assert query == "Томск"
        return geo.Place("Томск", 56.5, 84.97, "Asia/Tomsk")

    await feed("/city")
    monkeypatch.setattr(place.geo, "geocode", fake_geocode)
    sent = await feed("Томск")
    assert "Томск" in sent[-1].text


async def test_unknown_city_key_does_not_set_anything(feed, feed_callback, user_id) -> None:
    await feed("/city")
    sent = await feed_callback("city:нет-такого")
    alert = next(s for s in sent if s.method == "AnswerCallbackQuery")
    assert alert.data["text"] == texts.CITY_GONE
    assert db.get_user(user_id) is None
