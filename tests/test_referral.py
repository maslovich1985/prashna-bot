"""Рефералка: колонки, deep-link, защита от накрутки (G-01)."""

from __future__ import annotations

from typing import Any

import pytest

from app import db, geo, llm
from app.constants import REFERRAL_BONUS, REFERRAL_MAX, TRIAL_QUESTIONS
from app.handlers.basic import referrer_from

INVITER = 601
NEWCOMER = 602


@pytest.fixture(autouse=True)
def no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _boom(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("тест полез в сеть")

    monkeypatch.setattr(geo, "geocode", _boom)
    monkeypatch.setattr(llm, "interpret", _boom)


@pytest.fixture
def inviter() -> int:
    db.upsert_user(INVITER, "inviter")
    return INVITER


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        ("ref601", 601),
        ("ref0", 0),
        ("", None),
        ("601", None),
        ("refabc", None),
        ("ref-5", None),
        ("ref 601", None),
    ],
)
def test_deep_link_payload_is_parsed(payload: str, expected: int | None) -> None:
    assert referrer_from(payload) == expected


def test_referral_is_recorded_for_a_newcomer(inviter: int) -> None:
    assert db.note_referral(NEWCOMER, inviter) is True
    assert db.get_user(NEWCOMER)["referrer_id"] == inviter
    assert db.referrals_of(inviter) == 1


def test_known_user_is_not_reinvited(inviter: int) -> None:
    # Иначе старого пользователя переприглашают под разными ссылками без конца.
    db.upsert_user(NEWCOMER, "old")
    assert db.note_referral(NEWCOMER, inviter) is False
    assert db.get_user(NEWCOMER)["referrer_id"] is None


def test_referrer_is_written_only_once(inviter: int) -> None:
    other = 603
    db.upsert_user(other, "other")
    db.note_referral(NEWCOMER, inviter)
    assert db.note_referral(NEWCOMER, other) is False
    assert db.get_user(NEWCOMER)["referrer_id"] == inviter


def test_self_invite_is_rejected() -> None:
    db.upsert_user(NEWCOMER, "self")
    assert db.note_referral(NEWCOMER, NEWCOMER) is False
    assert db.get_user(NEWCOMER)["referrer_id"] is None


def test_unknown_referrer_creates_nothing() -> None:
    # Ссылка со случайным числом не должна заводить связь и будущий бонус.
    assert db.note_referral(NEWCOMER, 999999) is False
    assert db.get_user(NEWCOMER) is None


async def test_start_with_a_deep_link_records_the_referrer(feed, user_id, inviter: int) -> None:
    await feed(f"/start ref{inviter}")
    assert db.get_user(user_id)["referrer_id"] == inviter


async def test_plain_start_records_nothing(feed, user_id, inviter: int) -> None:
    await feed("/start")
    assert db.get_user(user_id)["referrer_id"] is None


async def test_second_start_does_not_rewrite_the_referrer(feed, user_id, inviter: int) -> None:
    await feed(f"/start ref{inviter}")
    other = 604
    db.upsert_user(other, "other")
    await feed(f"/start ref{other}")
    assert db.get_user(user_id)["referrer_id"] == inviter


async def test_self_invite_through_start_is_ignored(feed, user_id) -> None:
    await feed(f"/start ref{user_id}")
    assert db.get_user(user_id)["referrer_id"] is None


def test_referral_paid_starts_at_zero(inviter: int) -> None:
    db.note_referral(NEWCOMER, inviter)
    assert db.get_user(NEWCOMER)["referral_paid"] == 0


# --- G-02: начисление бонуса ----------------------------------------------- #


def _ask(user_id: int) -> None:
    """Один вопрос, доведённый до конца: резерв + commit."""
    res, reason = db.reserve(user_id, cooldown=0)
    assert res is not None, reason
    db.commit(res)


def test_bonus_lands_after_the_first_question(inviter: int) -> None:
    db.note_referral(NEWCOMER, inviter)
    # /start бонуса не даёт: платим за первый доведённый до конца вопрос.
    assert db.get_entitlement(inviter) is None

    _ask(NEWCOMER)
    assert db.get_entitlement(inviter)["questions_left"] == REFERRAL_BONUS


def test_bonus_is_paid_once(inviter: int) -> None:
    db.note_referral(NEWCOMER, inviter)
    _ask(NEWCOMER)
    _ask(NEWCOMER)
    assert db.get_entitlement(inviter)["questions_left"] == REFERRAL_BONUS


def test_bonus_goes_to_questions_not_trials(inviter: int) -> None:
    # trial_used обнуляется при /delete_me, а заработанное — нет.
    db.note_referral(NEWCOMER, inviter)
    _ask(NEWCOMER)
    row = db.get_entitlement(inviter)
    assert row["trial_used"] == 0
    assert row["questions_left"] == REFERRAL_BONUS


def test_invitee_gets_nothing_extra(inviter: int) -> None:
    db.note_referral(NEWCOMER, inviter)
    _ask(NEWCOMER)
    left = db.entitlement_for(NEWCOMER).left
    assert db.entitlement_for(NEWCOMER).source == "trial"
    assert left == TRIAL_QUESTIONS - 1


def test_bonus_stops_at_the_limit(inviter: int) -> None:
    for i in range(REFERRAL_MAX + 2):
        invitee = 700 + i
        db.note_referral(invitee, inviter)
        _ask(invitee)
    assert db.get_entitlement(inviter)["questions_left"] == REFERRAL_BONUS * REFERRAL_MAX


def test_commit_reports_the_referrer(inviter: int) -> None:
    # Уведомление приглашающему — G-03; commit отдаёт, кому его слать.
    db.note_referral(NEWCOMER, inviter)
    res, _ = db.reserve(NEWCOMER, cooldown=0)
    assert db.commit(res) == inviter

    res, _ = db.reserve(NEWCOMER, cooldown=0)
    assert db.commit(res) is None


def test_deleting_and_returning_pays_nothing_twice(inviter: int) -> None:
    """Журнал выплат переживает /delete_me — иначе удаление приносит бонус снова."""
    db.note_referral(NEWCOMER, inviter)
    _ask(NEWCOMER)
    db.delete_user_data(NEWCOMER)

    db.note_referral(NEWCOMER, inviter)
    _ask(NEWCOMER)
    assert db.get_entitlement(inviter)["questions_left"] == REFERRAL_BONUS


def test_question_without_a_referrer_pays_nobody(user_id: int) -> None:
    db.upsert_user(user_id, "solo")
    _ask(user_id)
    assert db.get_entitlement(user_id)["questions_left"] == 0
