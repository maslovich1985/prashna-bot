"""Необратимое удаление данных: /delete_me (E-03)."""

from __future__ import annotations

import pytest

from app import db, texts
from app.constants import TRIAL_QUESTIONS
from app.handlers import privacy


@pytest.fixture
def with_data(user_id: int) -> None:
    db.upsert_user(user_id, "tester", place="Томск", lat=56.5, lon=84.97, tz="Asia/Tomsk")
    db.save_prashna(user_id, "Вопрос?", 10, "Томск", "КАРТА", "ОТВЕТ")
    res, _ = db.reserve(user_id, cooldown=0)
    db.commit(res)


async def test_confirmation_is_required(feed, with_data, user_id) -> None:
    sent = await feed("/delete_me")
    assert sent[0].data.get("reply_markup") is not None
    # До нажатия кнопки ничего не удаляется.
    assert db.history(user_id, 10) != []


async def test_cancel_deletes_nothing(feed, feed_callback, with_data, user_id) -> None:
    await feed("/delete_me")
    sent = await feed_callback(privacy.CANCEL)
    assert any(texts.DELETE_ME_CANCELLED in s.text for s in sent)
    assert db.history(user_id, 10) != []
    assert db.get_user(user_id) is not None


async def test_confirm_removes_personal_data(feed, feed_callback, with_data, user_id) -> None:
    await feed("/delete_me")
    await feed_callback(privacy.CONFIRM)
    assert db.history(user_id, 10) == []
    assert db.get_user(user_id) is None
    with db.conn() as c:
        assert (
            c.execute("SELECT COUNT(*) FROM usage WHERE user_id=?", (user_id,)).fetchone()[0] == 0
        )


async def test_trials_are_not_reissued(feed, feed_callback, user_id) -> None:
    """Иначе /delete_me — способ получать пробные заново без конца."""
    for _ in range(TRIAL_QUESTIONS):
        res, _ = db.reserve(user_id, cooldown=0)
        db.commit(res)
    assert db.entitlement_for(user_id).source == "none"

    await feed("/delete_me")
    await feed_callback(privacy.CONFIRM)

    assert db.entitlement_for(user_id).source == "none"
    assert db.get_entitlement(user_id)["trial_used"] == TRIAL_QUESTIONS


async def test_payments_survive(feed, feed_callback, user_id) -> None:
    # refundStarPayment требует charge_id, плюс это бухгалтерия. Текстов вопросов там нет.
    db.grant(user_id, "ch-1", "pack10", 100)
    await feed("/delete_me")
    await feed_callback(privacy.CONFIRM)
    payment = db.get_payment("ch-1")
    assert payment is not None and payment["stars"] == 100


async def test_paid_access_is_burned_and_warned_about(feed, feed_callback, user_id) -> None:
    db.grant(user_id, "ch-1", "pack10", 100)
    sent = await feed("/delete_me")
    # Цена решения названа до кнопки, а не после.
    assert "сожжёт" in sent[0].text

    await feed_callback(privacy.CONFIRM)
    assert db.get_entitlement(user_id)["questions_left"] == 0


async def test_delete_me_is_in_the_menu() -> None:
    from app import bot as bot_module

    assert "delete_me" in [c.command for c in bot_module.BOT_COMMANDS]
