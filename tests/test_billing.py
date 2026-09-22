"""Оплата звёздами: /subscribe и инвойс (D-01), pre_checkout (D-02), выдача (D-03)."""

from __future__ import annotations

import dataclasses
import time
from typing import Any

import pytest

from app import alerts, db, geo, llm, texts
from app.config import settings
from app.constants import PLANS, Plan
from app.handlers import billing

PRICED = {
    "month": dataclasses.replace(PLANS["month"], stars=150),
    "pack10": dataclasses.replace(PLANS["pack10"], stars=100),
}


@pytest.fixture
def priced(monkeypatch: pytest.MonkeyPatch) -> None:
    """Цены в PLANS — заглушка (§15.1), поэтому тарифы для теста проставляем сами."""
    monkeypatch.setattr(billing, "PLANS", PRICED)


def _buttons(sent) -> list[tuple[str, str]]:
    # Стаб сессии отдаёт вызов через model_dump, поэтому клавиатура — словари, не модели.
    kb = sent.data["reply_markup"]["inline_keyboard"]
    return [(b["text"], b["callback_data"]) for row in kb for b in row]


async def test_subscribe_without_prices_refuses(feed) -> None:
    # Пока цены не проставлены, инвойс выставлять нечем — но и молчать нельзя.
    sent = await feed("/subscribe")
    assert [s.text for s in sent] == [texts.SALES_CLOSED]
    assert sent[0].data.get("reply_markup") is None


async def test_subscribe_lists_sellable_plans(feed, priced) -> None:
    sent = await feed("/subscribe")
    assert sent[0].text == texts.SUBSCRIBE_HEADER
    assert _buttons(sent[0]) == [
        ("Подписка на месяц — 150 ⭐️", "plan:month"),
        ("10 вопросов — 100 ⭐️", "plan:pack10"),
    ]


async def test_unsellable_plan_is_not_offered(feed, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(billing, "PLANS", {**PRICED, "year": PLANS["year"]})
    sent = await feed("/subscribe")
    assert "year" not in str(_buttons(sent[0]))


async def test_button_sends_invoice_in_stars(feed_callback, priced) -> None:
    sent = await feed_callback("plan:month")
    invoice = next(s for s in sent if s.method == "SendInvoice")
    assert invoice.data["currency"] == "XTR"
    assert invoice.data["payload"] == "month"
    assert invoice.data["title"] == "Подписка на месяц"
    # Сумма в XTR передаётся как есть: умножение на 100 — правило фиатных валют.
    assert invoice.data["prices"] == [{"label": "Подписка на месяц", "amount": 150}]
    # provider_token для звёзд не передаётся вовсе.
    assert "provider_token" not in invoice.data


async def test_invoice_describes_the_plan(feed_callback, priced) -> None:
    sent = await feed_callback("plan:pack10")
    invoice = next(s for s in sent if s.method == "SendInvoice")
    assert invoice.data["description"] == texts.plan_description(PRICED["pack10"])


async def test_unknown_plan_gets_alert_not_invoice(feed_callback, priced) -> None:
    sent = await feed_callback("plan:nope")
    assert not [s for s in sent if s.method == "SendInvoice"]
    alert = next(s for s in sent if s.method == "AnswerCallbackQuery")
    assert alert.data["text"] == texts.PLAN_GONE


async def test_plan_without_price_gets_alert(
    feed_callback, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Кнопка из старого сообщения, а цену тем временем сняли.
    monkeypatch.setattr(billing, "PLANS", {"month": Plan(title="Подписка на месяц", days=30)})
    sent = await feed_callback("plan:month")
    assert not [s for s in sent if s.method == "SendInvoice"]


# --- D-02: pre_checkout ---------------------------------------------------- #


async def test_pre_checkout_confirms_a_valid_payment(feed_pre_checkout, priced) -> None:
    sent = await feed_pre_checkout("month", 150)
    assert [(s.method, s.data["ok"]) for s in sent] == [("AnswerPreCheckoutQuery", True)]


async def test_pre_checkout_rejects_unknown_payload(feed_pre_checkout, priced) -> None:
    sent = await feed_pre_checkout("nope", 150)
    assert sent[0].data["ok"] is False
    assert sent[0].data["error_message"] == texts.PLAN_GONE


async def test_pre_checkout_rejects_wrong_amount(feed_pre_checkout, priced) -> None:
    # Сумму присылает Telegram, а не наш инвойс: расхождение с тарифом — отказ.
    sent = await feed_pre_checkout("month", 1)
    assert sent[0].data["ok"] is False
    assert sent[0].data["error_message"] == texts.CHECKOUT_AMOUNT_MISMATCH


async def test_pre_checkout_rejects_other_currency(feed_pre_checkout, priced) -> None:
    sent = await feed_pre_checkout("month", 150, currency="RUB")
    assert sent[0].data["ok"] is False


async def test_pre_checkout_touches_no_external_service(
    feed_pre_checkout, priced, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Ответ обязан уйти за 10 секунд, поэтому ни Groq, ни геокодера внутри быть не может."""

    async def _boom(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("pre_checkout полез в сеть")

    monkeypatch.setattr(geo, "geocode", _boom)
    monkeypatch.setattr(llm, "interpret", _boom)

    started = time.monotonic()
    sent = await feed_pre_checkout("month", 150)
    assert sent[0].data["ok"] is True
    assert time.monotonic() - started < 1.0


# --- D-03: выдача доступа -------------------------------------------------- #


@pytest.fixture
def admin(monkeypatch: pytest.MonkeyPatch) -> None:
    alerts.reset()
    monkeypatch.setattr(alerts, "settings", dataclasses.replace(settings, admin_ids=(999,)))


async def test_successful_payment_grants_access(feed_payment, user_id) -> None:
    sent = await feed_payment("pack10", "ch-1", 100)
    ent = db.entitlement_for(user_id)
    assert ent.source == "questions"
    assert ent.left == PLANS["pack10"].questions
    assert sent[0].text == texts.payment_done(PLANS["pack10"], ent)


async def test_repeated_payment_grants_once_and_stays_quiet(feed_payment, user_id) -> None:
    # Telegram присылает то же событие при ретраях и рестарте бота.
    await feed_payment("pack10", "ch-1", 100)
    sent = await feed_payment("pack10", "ch-1", 100)
    assert sent == []
    assert db.entitlement_for(user_id).left == PLANS["pack10"].questions


async def test_unknown_payload_alerts_admin_and_owns_up(feed_payment, admin, user_id) -> None:
    # Деньги списаны, тариф неизвестен: молчать нельзя ни перед кем.
    sent = await feed_payment("nope", "ch-9", 100)
    texts_sent = [s.text for s in sent]
    assert texts.payment_needs_support("ch-9") in texts_sent
    assert any("Оплачен неизвестный тариф" in t for t in texts_sent)
    assert db.entitlement_for(user_id).source == "trial"


# --- D-04: /terms и /paysupport -------------------------------------------- #


async def test_terms_explains_what_is_sold(feed) -> None:
    sent = await feed("/terms")
    assert sent[0].text == texts.TERMS
    # Главное правило прашны: возврат не покупает другой ответ.
    assert "возврат" in texts.TERMS.lower()


async def test_paysupport_shows_configured_contact(feed, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        billing, "settings", dataclasses.replace(settings, support_contact="@owner")
    )
    sent = await feed("/paysupport")
    assert "@owner" in sent[0].text


async def test_paysupport_works_without_contact(feed, monkeypatch: pytest.MonkeyPatch) -> None:
    # Без SUPPORT_CONTACT команда обязана ответить правилами, а не пустотой.
    monkeypatch.setattr(billing, "settings", dataclasses.replace(settings, support_contact=""))
    sent = await feed("/paysupport")
    assert "Контакт:" not in sent[0].text
    assert "/terms" in sent[0].text


def test_payment_commands_are_in_the_menu() -> None:
    from app import bot as bot_module

    commands = [c.command for c in bot_module.BOT_COMMANDS]
    assert "terms" in commands
    assert "paysupport" in commands
