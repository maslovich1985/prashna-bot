"""Возврат звёзд: db.refund (D-05)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app import db
from app.constants import PLANS

USER = 502
NOW = datetime(2026, 9, 22, 12, 0, tzinfo=timezone.utc)


def test_refund_revokes_the_subscription() -> None:
    db.grant(USER, "ch-1", "month", 150, now=NOW)
    assert db.refund("ch-1", now=NOW) is True
    # Отзыв зеркалит выдачу: те же 30 дней обратно, подписка снова неактивна.
    assert db.entitlement_for(USER, now=NOW).source == "trial"


def test_refund_keeps_the_rest_of_an_extended_subscription() -> None:
    db.grant(USER, "ch-1", "month", 150, now=NOW)
    db.grant(USER, "ch-2", "month", 150, now=NOW)
    db.refund("ch-2", now=NOW)
    expires = datetime.fromisoformat(db.get_entitlement(USER)["expires_at"])
    assert expires == NOW + timedelta(days=30)


def test_refund_takes_back_the_questions() -> None:
    db.grant(USER, "ch-1", "pack10", 100, now=NOW)
    db.refund("ch-1", now=NOW)
    assert db.get_entitlement(USER)["questions_left"] == 0


def test_refund_does_not_go_below_zero() -> None:
    # Часть вопросов пользователь мог потратить до возврата.
    db.grant(USER, "ch-1", "pack10", 100, now=NOW)
    with db.conn() as c:
        c.execute("UPDATE entitlements SET questions_left = 3 WHERE user_id = ?", (USER,))
    db.refund("ch-1", now=NOW)
    assert db.get_entitlement(USER)["questions_left"] == 0


def test_refund_marks_the_payment() -> None:
    db.grant(USER, "ch-1", "pack10", 100, now=NOW)
    db.refund("ch-1", now=NOW)
    assert db.get_payment("ch-1")["refunded_at"] == NOW.isoformat()


def test_second_refund_changes_nothing() -> None:
    db.grant(USER, "ch-1", "pack10", 100, now=NOW)
    assert db.refund("ch-1", now=NOW) is True
    assert db.refund("ch-1", now=NOW) is False
    assert db.get_entitlement(USER)["questions_left"] == 0


def test_refund_of_unknown_payment_is_a_bug() -> None:
    with pytest.raises(ValueError):
        db.refund("нет такого", now=NOW)


def test_granted_questions_survive_a_refund_of_another_pack() -> None:
    db.grant(USER, "ch-1", "pack10", 100, now=NOW)
    db.grant(USER, "ch-2", "pack10", 100, now=NOW)
    db.refund("ch-1", now=NOW)
    assert db.get_entitlement(USER)["questions_left"] == PLANS["pack10"].questions
