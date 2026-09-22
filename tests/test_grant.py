"""Выдача доступа за платёж: db.grant (D-03)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app import db
from app.constants import PLANS

USER = 501
NOW = datetime(2026, 9, 22, 12, 0, tzinfo=timezone.utc)


def _expires(user_id: int = USER) -> datetime:
    return datetime.fromisoformat(db.get_entitlement(user_id)["expires_at"])


def test_subscription_starts_from_now() -> None:
    assert db.grant(USER, "ch-1", "month", 150, now=NOW) is True
    assert _expires() == NOW + timedelta(days=30)
    assert db.entitlement_for(USER, now=NOW).source == "subscription"


def test_pack_adds_questions() -> None:
    db.grant(USER, "ch-1", "pack10", 100, now=NOW)
    ent = db.entitlement_for(USER, now=NOW)
    assert ent.source == "questions"
    assert ent.left == PLANS["pack10"].questions


def test_same_charge_id_grants_once() -> None:
    # Telegram присылает successful_payment заново при ретраях и рестарте бота.
    assert db.grant(USER, "ch-1", "pack10", 100, now=NOW) is True
    assert db.grant(USER, "ch-1", "pack10", 100, now=NOW) is False
    assert db.entitlement_for(USER, now=NOW).left == PLANS["pack10"].questions


def test_payment_over_active_subscription_extends_it() -> None:
    db.grant(USER, "ch-1", "month", 150, now=NOW)
    db.grant(USER, "ch-2", "month", 150, now=NOW + timedelta(days=10))
    # Остаток срока не сгорает: 30 дней прибавляются к прежней дате, а не к «сейчас».
    assert _expires() == NOW + timedelta(days=60)


def test_payment_over_expired_subscription_starts_from_now() -> None:
    db.grant(USER, "ch-1", "month", 150, now=NOW)
    later = NOW + timedelta(days=45)
    db.grant(USER, "ch-2", "month", 150, now=later)
    assert _expires() == later + timedelta(days=30)


def test_pack_does_not_reset_an_active_subscription() -> None:
    db.grant(USER, "ch-1", "month", 150, now=NOW)
    db.grant(USER, "ch-2", "pack10", 100, now=NOW)
    ent = db.entitlement_for(USER, now=NOW)
    assert ent.source == "subscription"
    assert _expires() == NOW + timedelta(days=30)
    assert db.get_entitlement(USER)["questions_left"] == PLANS["pack10"].questions


def test_payment_is_recorded() -> None:
    db.grant(USER, "ch-1", "month", 150, now=NOW)
    with db.conn() as c:
        row = dict(c.execute("SELECT * FROM payments WHERE charge_id = 'ch-1'").fetchone())
    assert row["user_id"] == USER
    assert row["plan"] == "month"
    assert row["stars"] == 150
    assert row["refunded_at"] is None


def test_unknown_plan_is_a_bug_not_a_silent_skip() -> None:
    with pytest.raises(ValueError):
        db.grant(USER, "ch-1", "nope", 150, now=NOW)
    with db.conn() as c:
        assert c.execute("SELECT COUNT(*) FROM payments").fetchone()[0] == 0
