"""Команды: /start, /help, /cancel, /me, /history, /chart, /forget, /stats.

/privacy и /delete_me живут в privacy.py — у приватности свой роутер.
"""

from __future__ import annotations

import html
from datetime import datetime

from aiogram import F, Router
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    BufferedInputFile,
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from .. import db, texts
from ..config import settings
from ..constants import TRIAL_QUESTIONS
from .billing import BUY_CALLBACK
from .common import main_kb, place_for

router = Router(name="basic")


def buy_kb() -> InlineKeyboardMarkup:
    # Кнопку обрабатывает billing: продажами владеет он, экран баланса только зовёт.
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=texts.BALANCE_BUY_BUTTON, callback_data=BUY_CALLBACK)]
        ]
    )


HELP_PREFIX = "help:"

# Экран → текст. Ключ уезжает в callback_data, поэтому он короткий и латиницей.
HELP_SCREENS = {"what": texts.HELP_WHAT, "how": texts.HELP_HOW, "pay": texts.HELP_PAY}


def help_menu_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=texts.HELP_BTN_WHAT, callback_data=f"{HELP_PREFIX}what")],
            [InlineKeyboardButton(text=texts.HELP_BTN_HOW, callback_data=f"{HELP_PREFIX}how")],
            [InlineKeyboardButton(text=texts.HELP_BTN_PAY, callback_data=f"{HELP_PREFIX}pay")],
        ]
    )


def help_back_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=texts.HELP_BTN_BACK, callback_data=f"{HELP_PREFIX}menu")]
        ]
    )


@router.message(CommandStart())
async def start(msg: Message, state: FSMContext) -> None:
    await state.clear()
    db.upsert_user(msg.from_user.id, msg.from_user.username)
    await msg.answer(texts.WELCOME, reply_markup=main_kb())


@router.message(Command("help"))
async def help_cmd(msg: Message) -> None:
    await msg.answer(texts.HELP_MENU, reply_markup=help_menu_kb())


@router.callback_query(F.data.startswith(HELP_PREFIX))
async def help_screen(call: CallbackQuery) -> None:
    key = (call.data or "").removeprefix(HELP_PREFIX)
    await call.answer()
    if call.message is None:
        return
    if key == "menu":
        await call.message.edit_text(texts.HELP_MENU, reply_markup=help_menu_kb())
        return
    screen = HELP_SCREENS.get(key)
    if screen is None:
        # Кнопка из старого сообщения, экран успели переименовать.
        await call.message.edit_text(texts.HELP_MENU, reply_markup=help_menu_kb())
        return
    await call.message.edit_text(screen, reply_markup=help_back_kb())


@router.message(Command("cancel"))
async def cancel(msg: Message, state: FSMContext) -> None:
    await state.clear()
    await msg.answer(texts.CANCELLED, reply_markup=main_kb())


@router.message(Command("me"))
async def me(msg: Message) -> None:
    place = await place_for(msg.from_user.id)
    ent = db.entitlement_for(msg.from_user.id)
    left: object = texts.UNLIMITED if ent.unlimited else ent.left
    await msg.answer(
        texts.profile(
            html.escape(place.name), place.lat, place.lon, place.tz, left, settings.ayanamsa
        )
    )


@router.message(Command("history"))
async def history(msg: Message) -> None:
    rows = db.history(msg.from_user.id, 10)
    if not rows:
        await msg.answer(texts.HISTORY_EMPTY)
        return
    lines = [texts.HISTORY_HEADER]
    for r in rows:
        when = datetime.fromisoformat(r["asked_at"]).strftime("%d.%m.%Y %H:%M")
        lines.append(texts.history_row(r["id"], when, r["house"], html.escape(r["question"][:80])))
    lines.append(texts.HISTORY_HINT)
    await msg.answer("\n".join(lines))


@router.message(Command("chart"))
async def chart_cmd(msg: Message) -> None:
    arg = (msg.text or "").partition(" ")[2].strip()
    if not arg.isdigit():
        await msg.answer(texts.CHART_USAGE)
        return
    row = db.get_prashna(msg.from_user.id, int(arg))
    if not row:
        await msg.answer(texts.CHART_NOT_FOUND)
        return
    doc = BufferedInputFile(
        texts.chart_file(row["question"], row["chart_text"], row["answer"]).encode(),
        filename=texts.chart_filename(row["id"]),
    )
    await msg.answer_document(doc, caption=texts.chart_caption(row["id"]))


@router.message(Command("forget"))
async def forget(msg: Message) -> None:
    n = db.clear_history(msg.from_user.id)
    await msg.answer(texts.history_cleared(n))


@router.message(Command("stats"))
async def stats(msg: Message) -> None:
    if msg.from_user.id not in settings.admin_ids:
        # Молчаливый return выглядел как поломка бота, а не как отказ.
        await msg.answer(texts.STATS_DENIED)
        return
    s = db.stats()
    await msg.answer("\n".join(f"{k}: {v}" for k, v in s.items()))


# Кнопки постоянной клавиатуры приходят обычным текстом, поэтому ловим их здесь —
# до catch-all прашны, который принял бы подпись кнопки за вопрос. Состояние
# чистим: нажатие кнопки посреди ввода города означает, что ввод брошен.


@router.message(F.text == texts.BTN_ASK)
async def ask_button(msg: Message, state: FSMContext) -> None:
    await state.clear()
    await msg.answer(texts.ASK_PROMPT, reply_markup=main_kb())


@router.message(F.text == texts.BTN_BALANCE)
async def balance_button(msg: Message, state: FSMContext) -> None:
    await state.clear()
    ent = db.entitlement_for(msg.from_user.id)
    row = db.get_entitlement(msg.from_user.id) or {}
    used = int(row.get("trial_used") or 0)
    await msg.answer(texts.balance(ent, used, TRIAL_QUESTIONS), reply_markup=buy_kb())


@router.message(F.text == texts.BTN_HISTORY)
async def history_button(msg: Message, state: FSMContext) -> None:
    await state.clear()
    await history(msg)


@router.message(F.text == texts.BTN_HELP)
async def help_button(msg: Message, state: FSMContext) -> None:
    await state.clear()
    await help_cmd(msg)
