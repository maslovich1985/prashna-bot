"""Ссылка «Пригласить друга» и уведомление о бонусе (G-03)."""

from __future__ import annotations

from typing import Any

import pytest

from app import db, geo, llm, texts
from app.astro import validity
from app.constants import REFERRAL_BONUS, REFERRAL_MAX
from app.handlers import prashna as prashna_handlers
from app.handlers.common import INVITE_CALLBACK, invite_link

INVITER = 611


@pytest.fixture(autouse=True)
def valid_prashna(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        prashna_handlers, "_validity_of", lambda *args, **kwargs: validity.Verdict()
    )


@pytest.fixture(autouse=True)
def no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _boom(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("тест полез в сеть")

    async def _interpret(*args: Any, **kwargs: Any) -> llm.Answer:
        return llm.Answer(text="ТОЛКОВАНИЕ")

    monkeypatch.setattr(geo, "geocode", _boom)
    monkeypatch.setattr(prashna_handlers.llm, "interpret", _interpret)


def test_link_carries_the_inviter(user_id: int) -> None:
    assert invite_link("prashna_bot", user_id) == f"https://t.me/prashna_bot?start=ref{user_id}"


async def test_balance_offers_the_invite_button(feed) -> None:
    sent = await feed(texts.BTN_BALANCE)
    kb = sent[0].data["reply_markup"]["inline_keyboard"]
    assert any(b["callback_data"] == INVITE_CALLBACK for row in kb for b in row)


async def test_invite_screen_shows_a_ready_link(feed, feed_callback, user_id) -> None:
    await feed(texts.BTN_BALANCE)
    sent = await feed_callback(INVITE_CALLBACK)
    text = sent[-1].text
    assert f"?start=ref{user_id}" in text
    assert str(REFERRAL_BONUS) in text
    assert str(REFERRAL_MAX) in text


async def test_invite_screen_counts_arrivals(feed, feed_callback, user_id) -> None:
    db.upsert_user(user_id, "inviter")
    db.note_referral(700, user_id)
    db.note_referral(701, user_id)
    await feed(texts.BTN_BALANCE)
    sent = await feed_callback(INVITE_CALLBACK)
    assert "<b>2</b>" in sent[-1].text


async def test_inviter_is_notified_after_the_first_question(feed, session, user_id, with_place):
    db.upsert_user(INVITER, "inviter")
    db.note_referral(user_id, INVITER)

    await feed("Получу ли я эту работу?")

    notice = next(s for s in session.sent if s.data.get("chat_id") == INVITER)
    assert texts.referral_paid(REFERRAL_BONUS) == notice.text


async def test_notification_hides_the_invitee(feed, session, user_id, with_place) -> None:
    """DoD: ни имени, ни user_id приглашённого в исходящем сообщении."""
    db.upsert_user(INVITER, "inviter")
    db.note_referral(user_id, INVITER)

    await feed("Получу ли я эту работу?")

    notice = next(s for s in session.sent if s.data.get("chat_id") == INVITER)
    assert str(user_id) not in notice.text
    assert "tester" not in notice.text


async def test_second_question_notifies_nobody(feed, session, user_id, with_place) -> None:
    db.upsert_user(INVITER, "inviter")
    db.note_referral(user_id, INVITER)
    await feed("Получу ли я эту работу?")
    before = len(session.sent)

    await feed("Вернётся ли ко мне долг?")
    assert not [s for s in session.sent[before:] if s.data.get("chat_id") == INVITER]


async def test_blocked_inviter_does_not_break_the_answer(
    feed, session, user_id, with_place, monkeypatch: pytest.MonkeyPatch
) -> None:
    from aiogram.exceptions import TelegramForbiddenError

    db.upsert_user(INVITER, "inviter")
    db.note_referral(user_id, INVITER)

    original = session.make_request

    async def maybe_blocked(bot, method, timeout=None):  # noqa: ASYNC109 — сигнатуру задаёт BaseSession
        if getattr(method, "chat_id", None) == INVITER:
            raise TelegramForbiddenError(method=method, message="bot was blocked")
        return await original(bot, method, timeout)

    monkeypatch.setattr(session, "make_request", maybe_blocked)
    sent = await feed("Получу ли я эту работу?")
    assert any("ТОЛКОВАНИЕ" in s.text for s in sent)
    # Бонус всё равно начислен: уведомление — не условие выплаты.
    assert db.get_entitlement(INVITER)["questions_left"] == REFERRAL_BONUS
