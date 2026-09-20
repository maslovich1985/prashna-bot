"""Команды: /start, /help, /cancel, /me, /history, /chart, /forget, /stats."""

from __future__ import annotations

import html
from datetime import datetime

from aiogram import Router
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import BufferedInputFile, Message, ReplyKeyboardRemove

from .. import db, texts
from ..config import settings
from .common import location_kb, place_for

router = Router(name="basic")


@router.message(CommandStart())
async def start(msg: Message, state: FSMContext) -> None:
    await state.clear()
    db.upsert_user(msg.from_user.id, msg.from_user.username)
    await msg.answer(texts.WELCOME, reply_markup=location_kb())


@router.message(Command("help"))
async def help_cmd(msg: Message) -> None:
    await msg.answer(texts.WELCOME)


@router.message(Command("cancel"))
async def cancel(msg: Message, state: FSMContext) -> None:
    await state.clear()
    await msg.answer(texts.CANCELLED, reply_markup=ReplyKeyboardRemove())


@router.message(Command("me"))
async def me(msg: Message) -> None:
    place = await place_for(msg.from_user.id)
    if msg.from_user.id in settings.admin_ids:
        left: object = texts.UNLIMITED
    else:
        left = db.remaining(msg.from_user.id, settings.daily_limit)
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
        (
            f"Вопрос: {row['question']}\n\n{row['chart_text']}\n\nТОЛКОВАНИЕ:\n{row['answer']}"
        ).encode(),
        filename=f"prashna_{row['id']}.txt",
    )
    await msg.answer_document(doc, caption=f"Прашна #{row['id']}")


@router.message(Command("forget"))
async def forget(msg: Message) -> None:
    n = db.clear_history(msg.from_user.id)
    await msg.answer(texts.forgotten(n))


@router.message(Command("stats"))
async def stats(msg: Message) -> None:
    if msg.from_user.id not in settings.admin_ids:
        # Молчаливый return выглядел как поломка бота, а не как отказ.
        await msg.answer(texts.STATS_DENIED)
        return
    s = db.stats()
    await msg.answer("\n".join(f"{k}: {v}" for k, v in s.items()))
