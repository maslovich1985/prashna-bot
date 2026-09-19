"""Основной путь: вопрос → карта → толкование. Плюс catch-all для всего остального."""

from __future__ import annotations

import asyncio
import html
import logging
from collections.abc import Iterator
from datetime import datetime, timezone

from aiogram import F, Router
from aiogram.enums import ChatAction
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from .. import db, llm, texts
from ..astro import constants as C
from ..astro.chart import build_chart
from ..astro.prashna import detect_house, judgment_factors, render_chart_text, render_short
from ..config import settings
from .common import place_for

log = logging.getLogger(__name__)
router = Router(name="prashna")

MIN_QUESTION_LEN = 8
MAX_QUESTION_LEN = 500
CHUNK = 3800  # лимит Telegram на сообщение — 4096, оставляем запас на разметку


def chunks(text: str, size: int) -> Iterator[str]:
    while text:
        if len(text) <= size:
            yield text
            return
        cut = text.rfind("\n", 0, size)
        if cut < size // 2:
            cut = size
        yield text[:cut]
        text = text[cut:].lstrip("\n")


@router.message(F.text & ~F.text.startswith("/"))
async def prashna(msg: Message, state: FSMContext) -> None:
    question = msg.text.strip()
    if len(question) < MIN_QUESTION_LEN:
        await msg.answer(texts.QUESTION_TOO_SHORT)
        return
    if len(question) > MAX_QUESTION_LEN:
        await msg.answer(texts.QUESTION_TOO_LONG)
        return

    if msg.from_user.id not in settings.admin_ids:
        ok, reason = db.check_and_bump(
            msg.from_user.id, settings.daily_limit, settings.cooldown_seconds
        )
        if not ok:
            await msg.answer(reason)
            return

    db.upsert_user(msg.from_user.id, msg.from_user.username)
    place = await place_for(msg.from_user.id)
    moment = datetime.now(timezone.utc)  # момент вопроса

    await msg.bot.send_chat_action(msg.chat.id, ChatAction.TYPING)

    try:
        house = detect_house(question)
        chart = await asyncio.to_thread(
            build_chart,
            moment,
            place.lat,
            place.lon,
            place.tz,
            place.name,
            house,
            settings.ayanamsa,
        )
        chart_text = render_chart_text(chart)
        factors = judgment_factors(chart)
    except Exception:
        log.exception("Ошибка расчёта карты")
        await msg.answer(texts.CHART_FAILED)
        return

    # Карта уходит до обращения к LLM: если толкование не придёт, у пользователя
    # всё равно останется расчёт на момент вопроса — повторить его уже нельзя.
    await msg.answer(
        texts.prashna_accepted(
            html.escape(question),
            chart.when_local.strftime("%d.%m.%Y %H:%M:%S"),
            place.tz,
            html.escape(place.name),
            render_short(chart),
        )
    )

    await msg.bot.send_chat_action(msg.chat.id, ChatAction.TYPING)
    try:
        answer = await llm.interpret(question, house, C.HOUSE_MEANINGS[house], chart_text, factors)
    except llm.LLMError as e:
        log.error("LLM: %s", e)
        await msg.answer(texts.LLM_UNAVAILABLE)
        return

    pid = db.save_prashna(msg.from_user.id, question, house, place.name, chart_text, answer)

    for chunk in chunks(html.escape(answer), CHUNK):
        await msg.answer(chunk)
    if msg.from_user.id in settings.admin_ids:
        left_txt = texts.UNLIMITED_PLAIN
    else:
        left_txt = str(db.remaining(msg.from_user.id, settings.daily_limit))
    await msg.answer(texts.prashna_footer(pid, left_txt))


@router.message()
async def fallback(msg: Message) -> None:
    await msg.answer(texts.FALLBACK)
