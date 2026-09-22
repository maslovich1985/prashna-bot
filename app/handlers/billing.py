"""Оплата звёздами: /subscribe → выбор тарифа → инвойс (D-01)."""

from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    LabeledPrice,
    Message,
)

from .. import texts
from ..constants import PLANS, Plan

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
        # payload проверяется по своей БД в pre_checkout (D-02): всё, что пришло
        # от Telegram, считаем недоверенным.
        payload=key,
        currency=CURRENCY,
        prices=[LabeledPrice(label=plan.title, amount=plan.stars)],
    )
