"""Дневной лимит и кулдаун — на реальном SQLite из фикстуры, без моков."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app import db


def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _seed(user_id: int, day: str, count: int, last_at: str | None = None) -> None:
    with db.conn() as c:
        c.execute(
            "INSERT INTO usage (user_id, day, count, last_at) VALUES (?,?,?,?)",
            (user_id, day, count, last_at),
        )


def test_limit_exhausted_on_next_call() -> None:
    for i in range(3):
        allowed, reason = db.check_and_bump(1, daily_limit=3, cooldown=0)
        assert allowed, f"вызов {i + 1} должен проходить: {reason}"
    allowed, reason = db.check_and_bump(1, daily_limit=3, cooldown=0)
    assert not allowed
    assert "лимит исчерпан" in reason.lower()


def test_rejected_call_does_not_increment() -> None:
    db.check_and_bump(1, daily_limit=1, cooldown=0)
    assert db.remaining(1, daily_limit=1) == 0
    db.check_and_bump(1, daily_limit=1, cooldown=0)
    with db.conn() as c:
        count = c.execute(
            "SELECT count FROM usage WHERE user_id=1 AND day=?", (_today(),)
        ).fetchone()["count"]
    assert count == 1


def test_cooldown_blocks_second_request() -> None:
    allowed, _ = db.check_and_bump(1, daily_limit=10, cooldown=30)
    assert allowed
    allowed, reason = db.check_and_bump(1, daily_limit=10, cooldown=30)
    assert not allowed
    assert "Подождите" in reason


def test_cooldown_expires() -> None:
    long_ago = (datetime.now(timezone.utc) - timedelta(seconds=60)).isoformat()
    _seed(1, _today(), count=1, last_at=long_ago)
    allowed, reason = db.check_and_bump(1, daily_limit=10, cooldown=30)
    assert allowed, reason


def test_counter_isolated_per_user() -> None:
    for _ in range(2):
        assert db.check_and_bump(1, daily_limit=2, cooldown=0)[0]
    assert not db.check_and_bump(1, daily_limit=2, cooldown=0)[0]
    assert db.check_and_bump(2, daily_limit=2, cooldown=0)[0]
    assert db.remaining(2, daily_limit=2) == 1


def test_counter_isolated_per_day() -> None:
    yesterday = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%d")
    _seed(1, yesterday, count=5, last_at=datetime.now(timezone.utc).isoformat())
    assert db.remaining(1, daily_limit=5) == 5
    allowed, reason = db.check_and_bump(1, daily_limit=5, cooldown=30)
    assert allowed, reason  # вчерашний last_at не должен включать кулдаун


@pytest.mark.parametrize("used", [0, 1, 3])
def test_remaining(used: int) -> None:
    if used:
        _seed(1, _today(), count=used)
    assert db.remaining(1, daily_limit=3) == 3 - used


def test_remaining_never_negative() -> None:
    _seed(1, _today(), count=9)
    assert db.remaining(1, daily_limit=3) == 0
