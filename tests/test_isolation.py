"""Изоляция данных между пользователями (E-04).

Утечки нет и сейчас — все запросы `app/db.py` идут по `user_id`, `/stats` закрыт
`admin_ids`. Эти тесты — регресс-защита: они падают, если рефакторинг потеряет
фильтр по пользователю.
"""

from __future__ import annotations

from typing import Any

import pytest

from app import db, geo, llm, texts

A = 777  # совпадает с отправителем по умолчанию в фикстурах
B = 888


@pytest.fixture(autouse=True)
def no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """Любой поход в сеть из-под этих тестов — ошибка, а не молчаливый запрос."""

    async def _boom(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("тест полез в сеть")

    monkeypatch.setattr(geo, "geocode", _boom)
    monkeypatch.setattr(llm, "interpret", _boom)


@pytest.fixture
def b_has_a_prashna() -> int:
    db.upsert_user(B, "bee", place="Омск", lat=55.0, lon=73.4, tz="Asia/Omsk")
    return db.save_prashna(B, "Секретный вопрос B?", 7, "Омск", "КАРТА B", "ОТВЕТ B")


async def test_chart_of_another_user_is_not_found(feed, b_has_a_prashna) -> None:
    sent = await feed(f"/chart {b_has_a_prashna}", user_id=A)
    assert [s.text for s in sent] == [texts.CHART_NOT_FOUND]


async def test_history_shows_only_own_questions(feed, b_has_a_prashna) -> None:
    db.save_prashna(A, "Вопрос A?", 1, "Томск", "КАРТА A", "ОТВЕТ A")
    sent = await feed("/history", user_id=A)
    joined = " ".join(s.text for s in sent)
    assert "Вопрос A?" in joined
    assert "Секретный вопрос B?" not in joined


async def test_answers_carry_no_foreign_user_id(feed, b_has_a_prashna) -> None:
    db.save_prashna(A, "Вопрос A?", 1, "Томск", "КАРТА A", "ОТВЕТ A")
    for command in ("/me", "/history", f"/chart {b_has_a_prashna}"):
        sent = await feed(command, user_id=A)
        assert all(str(B) not in s.text for s in sent), command


async def test_forget_clears_only_own_history(feed, b_has_a_prashna) -> None:
    db.save_prashna(A, "Вопрос A?", 1, "Томск", "КАРТА A", "ОТВЕТ A")
    await feed("/forget", user_id=A)
    assert db.history(A, 10) == []
    assert db.history(B, 10) != []


async def test_delete_me_touches_only_own_data(feed, feed_callback, b_has_a_prashna) -> None:
    from app.handlers import privacy

    await feed("/delete_me", user_id=A)
    await feed_callback(privacy.CONFIRM, user_id=A)
    assert db.get_user(B) is not None
    assert db.history(B, 10) != []


async def test_quota_is_counted_per_user(feed) -> None:
    res, _ = db.reserve(A, cooldown=0)
    db.commit(res)
    assert db.entitlement_for(A).left != db.entitlement_for(B).left


async def test_stats_stay_closed_to_a_plain_user(feed) -> None:
    sent = await feed("/stats", user_id=A)
    assert [s.text for s in sent] == [texts.STATS_DENIED]
