"""Рефералка: колонки, deep-link, защита от накрутки (G-01)."""

from __future__ import annotations

from typing import Any

import pytest

from app import db, geo, llm
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
