"""Рестарт между reserve и commit: висящий резерв возвращается при старте (§5.4.1)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app import db
from app.constants import TRIAL_QUESTIONS


def _reserved_row(user_id: int) -> dict:
    with db.conn() as c:
        row = c.execute(
            "SELECT reserved_at, reserved_src, count FROM usage WHERE user_id=?", (user_id,)
        ).fetchone()
    return dict(row)


def _age_reservation(user_id: int, seconds: int) -> None:
    old = (datetime.now(timezone.utc) - timedelta(seconds=seconds)).isoformat()
    with db.conn() as c:
        c.execute(
            "UPDATE usage SET reserved_at = ? WHERE user_id = ? AND reserved_at IS NOT NULL",
            (old, user_id),
        )


def test_reserve_marks_open_reservation() -> None:
    res, _ = db.reserve(1, cooldown=0)
    row = _reserved_row(1)
    assert row["reserved_at"] is not None
    assert row["reserved_src"] == res.source == "trial"


def test_commit_clears_the_mark() -> None:
    res, _ = db.reserve(1, cooldown=0)
    db.commit(res)
    assert _reserved_row(1)["reserved_at"] is None


def test_release_clears_the_mark() -> None:
    res, _ = db.reserve(1, cooldown=0)
    db.release(res)
    assert _reserved_row(1)["reserved_at"] is None


def test_stale_reservation_is_refunded() -> None:
    before = db.entitlement_for(1).left
    db.reserve(1, cooldown=0)
    _age_reservation(1, db.RESERVE_TTL + 60)

    assert db.release_stale() == 1
    assert db.entitlement_for(1).left == before
    assert _reserved_row(1)["reserved_at"] is None


def test_fresh_reservation_survives_restart() -> None:
    # Вопрос ещё обрабатывается — отбирать у него квант нельзя.
    db.reserve(1, cooldown=0)
    assert db.release_stale() == 0
    assert db.entitlement_for(1).left == TRIAL_QUESTIONS - 1


def test_committed_reservation_is_not_refunded() -> None:
    res, _ = db.reserve(1, cooldown=0)
    db.commit(res)
    _age_reservation(1, db.RESERVE_TTL + 60)  # пометки уже нет — старить нечего
    assert db.release_stale() == 0
    assert db.entitlement_for(1).left == TRIAL_QUESTIONS - 1


def test_package_quantum_returns_to_its_source() -> None:
    with db.conn() as c:
        c.execute(
            "INSERT INTO entitlements (user_id, questions_left, trial_used, updated_at) "
            "VALUES (?,?,?,?)",
            (1, 3, TRIAL_QUESTIONS, datetime.now(timezone.utc).isoformat()),
        )
    db.reserve(1, cooldown=0)
    _age_reservation(1, db.RESERVE_TTL + 60)
    db.release_stale()
    assert db.entitlement_for(1).left == 3


def test_second_run_finds_nothing() -> None:
    db.reserve(1, cooldown=0)
    _age_reservation(1, db.RESERVE_TTL + 60)
    assert db.release_stale() == 1
    assert db.release_stale() == 0
