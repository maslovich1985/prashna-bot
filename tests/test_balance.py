"""Экран «Баланс»: цифры совпадают с entitlement_for (F-05)."""

from __future__ import annotations

import dataclasses
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest

from app import db, geo, llm, texts
from app.constants import TRIAL_QUESTIONS
from app.handlers import billing


@pytest.fixture(autouse=True)
def no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _boom(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("тест полез в сеть")

    monkeypatch.setattr(geo, "geocode", _boom)
    monkeypatch.setattr(llm, "interpret", _boom)


def _expected(user_id: int) -> str:
    ent = db.entitlement_for(user_id)
    row = db.get_entitlement(user_id) or {}
    return texts.balance(ent, int(row.get("trial_used") or 0), TRIAL_QUESTIONS)


async def test_fresh_user_sees_trials(feed, user_id) -> None:
    sent = await feed(texts.BTN_BALANCE)
    assert sent[0].text == _expected(user_id)
    assert str(TRIAL_QUESTIONS) in sent[0].text


async def test_trial_counter_follows_usage(feed, user_id) -> None:
    res, _ = db.reserve(user_id, cooldown=0)
    db.commit(res)
    sent = await feed(texts.BTN_BALANCE)
    assert sent[0].text == _expected(user_id)
    assert f"1 из {TRIAL_QUESTIONS}" in sent[0].text


async def test_pack_shows_questions_left(feed, user_id) -> None:
    db.grant(user_id, "ch-1", "pack10", 100)
    sent = await feed(texts.BTN_BALANCE)
    ent = db.entitlement_for(user_id)
    assert sent[0].text == _expected(user_id)
    assert str(ent.left) in sent[0].text


async def test_subscription_shows_date_and_daily_left(feed, user_id) -> None:
    now = datetime.now(timezone.utc)
    db.grant(user_id, "ch-1", "month", 150, now=now)
    sent = await feed(texts.BTN_BALANCE)
    ent = db.entitlement_for(user_id)
    assert sent[0].text == _expected(user_id)
    assert (now + timedelta(days=30)).strftime("%d.%m.%Y") in sent[0].text
    assert str(ent.daily_limit) in sent[0].text


async def test_exhausted_trials_offer_access(feed, user_id) -> None:
    for _ in range(TRIAL_QUESTIONS):
        res, _ = db.reserve(user_id, cooldown=0)
        db.commit(res)
    sent = await feed(texts.BTN_BALANCE)
    assert db.entitlement_for(user_id).source == "none"
    assert sent[0].text == _expected(user_id)


async def test_admin_sees_unlimited(feed, user_id, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(db, "settings", dataclasses.replace(db.settings, admin_ids=(user_id,)))
    sent = await feed(texts.BTN_BALANCE)
    assert sent[0].text == _expected(user_id)


async def test_balance_offers_a_buy_button(feed) -> None:
    sent = await feed(texts.BTN_BALANCE)
    kb = sent[0].data["reply_markup"]["inline_keyboard"]
    assert kb[0][0]["callback_data"] == billing.BUY_CALLBACK


async def test_buy_button_opens_the_plans(feed, feed_callback) -> None:
    await feed(texts.BTN_BALANCE)
    sent = await feed_callback(billing.BUY_CALLBACK)
    # Цены пока не проставлены (§15.1), поэтому здесь отказ, а не список тарифов.
    assert any(texts.SALES_CLOSED in s.text for s in sent)


async def test_me_stays_technical(feed, user_id) -> None:
    sent = await feed("/me")
    assert "Аянамша" in sent[0].text
    assert "Баланс" not in sent[0].text
