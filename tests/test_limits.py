"""Дневной лимит и кулдаун — на реальном SQLite из фикстуры, без моков.

Счётчик суток крутит `reserve` (§5.4), поэтому проверяем его, а не отдельный вызов.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app import db
from app.constants import TRIAL_QUESTIONS


def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _seed_usage(user_id: int, day: str, count: int, last_at: str | None = None) -> None:
    with db.conn() as c:
        c.execute(
            "INSERT INTO usage (user_id, day, count, last_at) VALUES (?,?,?,?)",
            (user_id, day, count, last_at),
        )


def _subscriber(user_id: int, days: int = 30) -> None:
    """Суточный потолок есть только у подписки — пробные и пакет считаются квантами."""
    with db.conn() as c:
        c.execute(
            "INSERT INTO entitlements (user_id, plan, expires_at, trial_used, updated_at) "
            "VALUES (?, 'month', ?, ?, ?)",
            (
                user_id,
                (datetime.now(timezone.utc) + timedelta(days=days)).isoformat(),
                TRIAL_QUESTIONS,
                datetime.now(timezone.utc).isoformat(),
            ),
        )


def test_limit_exhausted_on_next_call() -> None:
    _subscriber(1)
    for i in range(10):
        res, reason = db.reserve(1, cooldown=0)
        assert res is not None, f"вызов {i + 1} должен проходить: {reason}"
    res, reason = db.reserve(1, cooldown=0)
    assert res is None
    assert "лимит исчерпан" in reason.lower()


def test_rejected_call_does_not_increment() -> None:
    _subscriber(1)
    _seed_usage(1, _today(), count=10)
    assert db.reserve(1, cooldown=0)[0] is None
    with db.conn() as c:
        count = c.execute(
            "SELECT count FROM usage WHERE user_id=1 AND day=?", (_today(),)
        ).fetchone()["count"]
    assert count == 10


def test_cooldown_blocks_second_request() -> None:
    _subscriber(1)
    assert db.reserve(1, cooldown=30)[0] is not None
    res, reason = db.reserve(1, cooldown=30)
    assert res is None
    assert "Подождите" in reason


def test_cooldown_expires() -> None:
    _subscriber(1)
    long_ago = (datetime.now(timezone.utc) - timedelta(seconds=60)).isoformat()
    _seed_usage(1, _today(), count=1, last_at=long_ago)
    res, reason = db.reserve(1, cooldown=30)
    assert res is not None, reason


def test_counter_isolated_per_user() -> None:
    _subscriber(1)
    _subscriber(2)
    _seed_usage(1, _today(), count=10)
    assert db.reserve(1, cooldown=0)[0] is None
    assert db.reserve(2, cooldown=0)[0] is not None
    assert db.entitlement_for(2).left == 9


def test_counter_isolated_per_day() -> None:
    _subscriber(1)
    yesterday = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%d")
    _seed_usage(1, yesterday, count=10, last_at=datetime.now(timezone.utc).isoformat())
    assert db.entitlement_for(1).left == 10
    # Вчерашний last_at не должен включать кулдаун.
    res, reason = db.reserve(1, cooldown=30)
    assert res is not None, reason


@pytest.mark.parametrize("used", [0, 1, 3])
def test_remaining(used: int) -> None:
    if used:
        _seed_usage(1, _today(), count=used)
    assert db.remaining(1, daily_limit=3) == 3 - used


def test_remaining_never_negative() -> None:
    _seed_usage(1, _today(), count=9)
    assert db.remaining(1, daily_limit=3) == 0
