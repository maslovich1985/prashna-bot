"""reserve / commit / release: квант списывается только за доставленное толкование."""

from __future__ import annotations

import dataclasses
from datetime import datetime, timedelta, timezone

import pytest

from app import db, texts
from app.constants import TRIAL_QUESTIONS


def _seed(user_id: int, **fields: object) -> None:
    cols = ", ".join(fields)
    marks = ", ".join("?" for _ in fields)
    with db.conn() as c:
        c.execute(
            f"INSERT INTO entitlements (user_id, updated_at, {cols}) VALUES (?, ?, {marks})",
            (user_id, datetime.now(timezone.utc).isoformat(), *fields.values()),
        )


def _iso(days: float) -> str:
    return (datetime.now(timezone.utc) + timedelta(days=days)).isoformat()


def test_trial_reserve_and_release_restores_quantum() -> None:
    before = db.entitlement_for(1)
    res, reason = db.reserve(1, cooldown=0)
    assert res is not None and reason == ""
    assert db.entitlement_for(1).left == before.left - 1
    db.release(res)
    assert db.entitlement_for(1).left == before.left


def test_trial_commit_keeps_quantum_spent() -> None:
    res, _ = db.reserve(1, cooldown=0)
    db.commit(res)
    assert db.entitlement_for(1).left == TRIAL_QUESTIONS - 1


def test_package_release_restores_question() -> None:
    _seed(1, questions_left=3, trial_used=TRIAL_QUESTIONS)
    res, _ = db.reserve(1, cooldown=0)
    assert db.entitlement_for(1).left == 2
    db.release(res)
    assert db.entitlement_for(1).left == 3


def test_subscription_release_restores_daily_count() -> None:
    _seed(1, plan="month", expires_at=_iso(10), trial_used=TRIAL_QUESTIONS)
    res, _ = db.reserve(1, cooldown=0)
    assert db.entitlement_for(1).left == 9
    db.release(res)
    assert db.entitlement_for(1).left == 10


def test_subscription_daily_limit_refuses() -> None:
    _seed(1, plan="month", expires_at=_iso(10), trial_used=TRIAL_QUESTIONS)
    for _ in range(10):
        assert db.reserve(1, cooldown=0)[0] is not None
    res, reason = db.reserve(1, cooldown=0)
    assert res is None
    assert reason == texts.daily_limit_reached(10)


def test_trial_has_no_daily_limit_only_quanta() -> None:
    for _ in range(TRIAL_QUESTIONS):
        assert db.reserve(1, cooldown=0)[0] is not None
    res, reason = db.reserve(1, cooldown=0)
    assert res is None
    assert reason == texts.NO_ENTITLEMENT


def test_cooldown_blocks_second_question() -> None:
    _seed(1, questions_left=5, trial_used=TRIAL_QUESTIONS)
    assert db.reserve(1, cooldown=30)[0] is not None
    res, reason = db.reserve(1, cooldown=30)
    assert res is None
    assert "Подождите" in reason
    # Отказ по кулдауну квант не трогает.
    assert db.entitlement_for(1).left == 4


def test_admin_reserve_charges_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(db, "settings", dataclasses.replace(db.settings, admin_ids=(7,)))
    res, _ = db.reserve(7, cooldown=30)
    assert res is not None and res.source == "admin"
    # Ни кулдауна, ни счётчика: второй вопрос подряд проходит.
    assert db.reserve(7, cooldown=30)[0] is not None
    db.release(res)


def test_refusal_when_nothing_left() -> None:
    _seed(1, questions_left=0, trial_used=TRIAL_QUESTIONS)
    res, reason = db.reserve(1, cooldown=0)
    assert res is None
    assert reason == texts.NO_ENTITLEMENT
