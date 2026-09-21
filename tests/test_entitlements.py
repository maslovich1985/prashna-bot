"""Приоритет прав §5.1 — на реальном SQLite из фикстуры."""

from __future__ import annotations

import dataclasses
from datetime import datetime, timedelta, timezone

import pytest

from app import db
from app.config import settings
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


def test_admin_unlimited(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(db, "settings", dataclasses.replace(db.settings, admin_ids=(7,)))
    ent = db.entitlement_for(7)
    assert (ent.source, ent.allowed, ent.unlimited) == ("admin", True, True)


def test_admin_wins_over_everything(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(db, "settings", dataclasses.replace(db.settings, admin_ids=(7,)))
    _seed(7, plan="pack10", questions_left=3, trial_used=TRIAL_QUESTIONS)
    assert db.entitlement_for(7).source == "admin"


def test_active_subscription() -> None:
    _seed(1, plan="month", expires_at=_iso(10), trial_used=TRIAL_QUESTIONS)
    ent = db.entitlement_for(1)
    assert ent.source == "subscription"
    assert ent.plan == "month"
    assert ent.daily_limit == 10
    assert ent.left == 10  # сегодня ещё ничего не спрошено


def test_subscription_left_follows_usage() -> None:
    _seed(1, plan="month", expires_at=_iso(10))
    db.check_and_bump(1, daily_limit=10, cooldown=0)
    assert db.entitlement_for(1).left == 9


def test_expired_subscription_falls_to_refusal() -> None:
    # Пробные не восстанавливаются, поэтому истёкшая подписка падает на отказ.
    _seed(1, plan="month", expires_at=_iso(-1), trial_used=TRIAL_QUESTIONS)
    assert db.entitlement_for(1).source == "none"


def test_expired_subscription_falls_to_package() -> None:
    _seed(1, plan="month", expires_at=_iso(-1), questions_left=4, trial_used=TRIAL_QUESTIONS)
    ent = db.entitlement_for(1)
    assert (ent.source, ent.left) == ("questions", 4)


def test_package_before_trial() -> None:
    _seed(1, questions_left=2, trial_used=0)
    assert db.entitlement_for(1).source == "questions"


def test_trial_for_new_user() -> None:
    ent = db.entitlement_for(999)
    assert (ent.source, ent.left, ent.daily_limit) == ("trial", TRIAL_QUESTIONS, 0)


def test_trial_partially_used() -> None:
    _seed(1, trial_used=1)
    assert db.entitlement_for(1).left == TRIAL_QUESTIONS - 1


def test_refusal_when_everything_spent() -> None:
    _seed(1, questions_left=0, trial_used=TRIAL_QUESTIONS)
    ent = db.entitlement_for(1)
    assert (ent.source, ent.allowed, ent.unlimited) == ("none", False, False)


def test_now_is_injectable() -> None:
    _seed(1, plan="month", expires_at=_iso(5), trial_used=TRIAL_QUESTIONS)
    later = datetime.now(timezone.utc) + timedelta(days=6)
    assert db.entitlement_for(1, now=later).source == "none"


def test_settings_admin_ids_untouched() -> None:
    # Права читаются из настроек, а не из захардкоженного списка.
    assert isinstance(settings.admin_ids, tuple)
