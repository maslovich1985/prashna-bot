"""Оплата звёздами: инвойс (D-01), pre-checkout (D-02), выдача (D-03), возврат (D-05)."""

from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.exceptions import TelegramAPIError
from aiogram.filters import Command
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    LabeledPrice,
    Message,
    PreCheckoutQuery,
)

from .. import alerts, db, texts
from ..config import settings
from ..constants import PLANS, Plan

log = logging.getLogger(__name__)

router = Router(name="billing")

CALLBACK_PREFIX = "plan:"

# Валюта звёзд. В XTR сумма передаётся как есть, без умножения на 100, — в отличие
# от фиатных валют Bot API.
CURRENCY = "XTR"


def plans_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=texts.plan_button(plan.title, plan.stars),
                    callback_data=f"{CALLBACK_PREFIX}{key}",
                )
            ]
            for key, plan in PLANS.items()
            if plan.sellable
        ]
    )


def _sellable(key: str) -> Plan | None:
    plan = PLANS.get(key)
    return plan if plan is not None and plan.sellable else None


@router.message(Command("subscribe"))
async def subscribe(msg: Message) -> None:
    kb = plans_kb()
    if not kb.inline_keyboard:
        await msg.answer(texts.SALES_CLOSED)
        return
    await msg.answer(texts.SUBSCRIBE_HEADER, reply_markup=kb)


@router.message(Command("terms"))
async def terms(msg: Message) -> None:
    await msg.answer(texts.TERMS)


@router.message(Command("paysupport"))
async def paysupport(msg: Message) -> None:
    await msg.answer(texts.paysupport(settings.support_contact))


@router.callback_query(F.data.startswith(CALLBACK_PREFIX))
async def send_invoice(call: CallbackQuery) -> None:
    key = (call.data or "").removeprefix(CALLBACK_PREFIX)
    plan = _sellable(key)
    if plan is None or call.message is None:
        # Кнопка из старого сообщения, а тариф успели убрать или снять с продажи.
        await call.answer(texts.PLAN_GONE, show_alert=True)
        return
    await call.answer()
    await call.message.answer_invoice(
        title=plan.title,
        description=texts.plan_description(plan),
        # payload возвращается в pre_checkout и там перепроверяется: всё, что пришло
        # от Telegram, считаем недоверенным.
        payload=key,
        currency=CURRENCY,
        prices=[LabeledPrice(label=plan.title, amount=plan.stars)],
    )


@router.pre_checkout_query()
async def pre_checkout(query: PreCheckoutQuery) -> None:
    """Последняя возможность отказаться от платежа.

    Ответ обязан уйти за 10 секунд, иначе Telegram отменит платёж сам, — поэтому
    внутри только свои константы: ни Groq, ни геокодера, ни прокси.
    """
    plan = _sellable(query.invoice_payload)
    if plan is None:
        await query.answer(ok=False, error_message=texts.PLAN_GONE)
        return
    # Сумму и валюту присылает Telegram, а не наш инвойс: сверяем с тарифом.
    if query.currency != CURRENCY or query.total_amount != plan.stars:
        await query.answer(ok=False, error_message=texts.CHECKOUT_AMOUNT_MISMATCH)
        return
    await query.answer(ok=True)


@router.message(F.successful_payment)
async def successful_payment(msg: Message) -> None:
    """Деньги уже списаны: наша задача — выдать доступ ровно один раз."""
    payment = msg.successful_payment
    charge_id = payment.telegram_payment_charge_id
    plan = PLANS.get(payment.invoice_payload)
    if plan is None:
        # Досюда доходит только то, что пропустил pre_checkout, но деньги уже
        # списаны — молчать нельзя ни перед пользователем, ни перед админом.
        log.error("Оплачен неизвестный тариф %s, charge_id=%s", payment.invoice_payload, charge_id)
        await alerts.notify(
            msg.bot,
            f"payment:{charge_id}",
            texts.alert_unknown_plan(msg.from_user.id, payment.invoice_payload, charge_id),
        )
        await msg.answer(texts.payment_needs_support(charge_id))
        return

    granted = db.grant(msg.from_user.id, charge_id, payment.invoice_payload, payment.total_amount)
    if not granted:
        # Повторная доставка того же платежа: доступ уже выдан, второй раз не выдаём
        # и вторым сообщением не поздравляем.
        log.info("Повторный successful_payment, charge_id=%s", charge_id)
        return

    ent = db.entitlement_for(msg.from_user.id)
    await msg.answer(texts.payment_done(plan, ent))


@router.message(Command("refund"))
async def refund(msg: Message) -> None:
    """Возврат звёзд админской командой: `/refund <charge_id>`.

    Сначала Telegram возвращает деньги, и только потом отзывается доступ: обратный
    порядок оставил бы пользователя без доступа и без денег, если возврат не прошёл.
    """
    if msg.from_user.id not in settings.admin_ids:
        await msg.answer(texts.REFUND_DENIED)
        return

    charge_id = (msg.text or "").partition(" ")[2].strip()
    if not charge_id:
        await msg.answer(texts.REFUND_USAGE)
        return

    payment = db.get_payment(charge_id)
    if payment is None:
        await msg.answer(texts.refund_unknown(charge_id))
        return
    if payment["refunded_at"]:
        await msg.answer(texts.refund_already(charge_id, payment["refunded_at"]))
        return

    try:
        await msg.bot.refund_star_payment(payment["user_id"], charge_id)
    except TelegramAPIError as e:
        # Деньги не вернулись — доступ не трогаем, иначе он пропадёт впустую.
        log.exception("Возврат %s не прошёл", charge_id)
        await msg.answer(texts.refund_failed(charge_id, str(e)))
        return

    db.refund(charge_id)
    await msg.answer(texts.refund_done(charge_id, payment["user_id"], payment["stars"]))

    try:
        await msg.bot.send_message(payment["user_id"], texts.refund_notice(payment["stars"]))
    except TelegramAPIError:
        # Пользователь мог заблокировать бота: возврат уже состоялся, это не ошибка.
        log.warning("Не удалось сообщить о возврате пользователю %s", payment["user_id"])
