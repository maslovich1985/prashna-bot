"""Основной путь: вопрос → карта → толкование. Плюс catch-all для всего остального."""

from __future__ import annotations

import asyncio
import html
import logging
from collections.abc import Iterator
from contextlib import suppress
from datetime import datetime, timezone

from aiogram import F, Router
from aiogram.enums import ChatAction
from aiogram.exceptions import TelegramAPIError
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from .. import alerts, db, llm, texts
from ..astro import constants as C
from ..astro import validity
from ..astro.chart import PrashnaChart, build_chart, sky_at
from ..astro.prashna import detect_house, judgment_factors, render_chart_text, render_short
from ..config import settings
from ..constants import CONSULT_PRICE_MAX, CONSULT_PRICE_MIN, REFERRAL_BONUS
from ..geo import Place
from .common import buy_kb, looks_like_city, place_for
from .place import cities_kb, pending_city_kb

log = logging.getLogger(__name__)
router = Router(name="prashna")

# Таблица §5.4.1: что показать пользователю на каждый вид сбоя толкования.
LLM_FAILURE_TEXTS = {
    llm.LLMRateLimited: texts.LLM_RATE_LIMITED,
    llm.LLMTimeout: texts.LLM_TIMEOUT,
    llm.LLMEmptyAnswer: texts.LLM_EMPTY,
}

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

    # Рестарт бота теряет FSM: человек, застигнутый на шаге «напишите город»,
    # отправляет название — и оно ушло бы в расчёт как вопрос. Переспрашиваем,
    # ничего не списывая (F-08).
    if looks_like_city(question):
        await state.update_data(pending_city=question)
        await msg.answer(texts.city_or_question(question), reply_markup=pending_city_kb())
        return

    if len(question) < MIN_QUESTION_LEN:
        await msg.answer(texts.QUESTION_TOO_SHORT)
        return
    if len(question) > MAX_QUESTION_LEN:
        await msg.answer(texts.QUESTION_TOO_LONG)
        return

    # Отказ не списывает квант, но считает карту — это CPU. Потолок проверяем
    # раньше всего: превышен — не считаем ничего (§5.5.3).
    is_admin = msg.from_user.id in settings.admin_ids
    if not is_admin and db.rejects_today(msg.from_user.id) >= C.MAX_REJECTS_PER_DAY:
        await msg.answer(texts.TOO_MANY_REJECTS)
        return

    # Вопрос без ясного дома виден по одному тексту: отказываем до резерва и до
    # расчёта карты — ни кванта, ни CPU (§5.5.1, правило 1).
    house_verdict = validity.check_question(question)
    if house_verdict.rejected:
        if not is_admin:
            db.note_reject(msg.from_user.id)
        await msg.answer(texts.prashna_rejected(house_verdict.reason, None))
        return

    db.upsert_user(msg.from_user.id, msg.from_user.username)
    # Место нужно для лагны, поэтому геоданные берём до резерва: иначе их сбой
    # списал бы квант ни за что.
    place = await place_for(msg.from_user.id)
    if place is None:
        # Место определяет лагну, то есть ответ. Молча подставить Москву — брак:
        # человек получил бы карту чужого города и не узнал об этом. Проверка до
        # reserve, поэтому квант не списывается.
        await msg.answer(texts.NO_PLACE, reply_markup=cities_kb())
        return

    res, reason = db.reserve(msg.from_user.id, settings.cooldown_seconds)
    if res is None:
        # Кончились пробные — момент решения, а не сухой отказ: показываем, с чем
        # сравнивать. Кулдаун и суточный лимит остаются обычным сообщением.
        if not db.entitlement_for(msg.from_user.id).allowed:
            await msg.answer(
                texts.sales_pitch(CONSULT_PRICE_MIN, CONSULT_PRICE_MAX), reply_markup=buy_kb()
            )
        else:
            await msg.answer(reason)
        return

    committed = False
    try:
        committed = await _answer(msg, question, place)
    finally:
        # Именно finally с флагом, а не except по списку типов: неучтённое
        # исключение тоже обязано вернуть квант.
        if committed:
            referrer_id = db.commit(res)
            if referrer_id is not None:
                await _notify_referrer(msg, referrer_id)
        else:
            db.release(res)


async def _notify_referrer(msg: Message, referrer_id: int) -> None:
    """Сообщает пригласившему о бонусе. Ни имени, ни id приглашённого: он не давал
    согласия на раскрытие того, что обращался к астрологическому боту."""
    try:
        await msg.bot.send_message(referrer_id, texts.referral_paid(REFERRAL_BONUS))
    except TelegramAPIError:
        # Заблокировал бота или удалил чат: бонус начислен, это не повод падать.
        log.warning("Не удалось уведомить пригласившего %s", referrer_id)


def _validity_of(
    user_id: int, question: str, chart: PrashnaChart, place: Place
) -> validity.Verdict:
    """Проверка валидности целиком в потоке: перебор моментов для `retry_at` — это CPU."""
    history = [
        validity.PastAsk(pid=row["id"], question=row["question"], asc_sign=row["asc_sign"])
        for row in db.recent_prashna(user_id, C.REPEAT_WINDOW_HOURS)
    ]
    return validity.check(
        chart,
        question,
        user_id,
        history=history,
        sky_at=lambda moment: sky_at(moment, place.lat, place.lon, settings.ayanamsa),
    )


async def _answer(msg: Message, question: str, place: Place) -> bool:
    """Карта → толкование → доставка. True = толкование дошло, квант списан по делу."""
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
        verdict = await asyncio.to_thread(_validity_of, msg.from_user.id, question, chart, place)
    except Exception as e:
        log.exception("Ошибка расчёта карты")
        await alerts.notify(
            msg.bot, "chart_failed", texts.alert_chart_failed(msg.from_user.id, repr(e))
        )
        await msg.answer(texts.CHART_FAILED)
        return False

    # Карта уходит до обращения к LLM: если толкование не придёт, у пользователя
    # всё равно останется расчёт на момент вопроса — повторить его уже нельзя.
    # По той же причине карту показываем и при отказе: видно, что расчёт был.
    await msg.answer(
        texts.prashna_accepted(
            html.escape(question),
            chart.when_local.strftime("%d.%m.%Y %H:%M:%S"),
            place.tz,
            html.escape(place.name),
            render_short(chart),
        )
    )

    if verdict.rejected:
        retry_local = (
            verdict.retry_at.astimezone(chart.when_local.tzinfo).strftime("%H:%M")
            if verdict.retry_at
            else None
        )
        db.save_prashna(
            msg.from_user.id,
            question,
            house,
            place.name,
            chart_text,
            answer="",
            asc_sign=chart.asc_sign,
            reject_reason=verdict.reason,
        )
        if msg.from_user.id not in settings.admin_ids:
            db.note_reject(msg.from_user.id)
        await msg.answer(texts.prashna_rejected(verdict.reason, retry_local))
        # LLM не зовём, квант вернётся в finally: отказ не должен стоить вопроса.
        return False

    await msg.bot.send_chat_action(msg.chat.id, ChatAction.TYPING)
    try:
        answer = await llm.interpret(question, house, C.HOUSE_MEANINGS[house], chart_text, factors)
    except llm.LLMError as e:
        # Ни одна ветка не списывает вопрос: толкования не было (§5.4.1).
        if isinstance(e, llm.LLMAuthError):
            # Ключ протух или кончился биллинг — сервис стоит целиком, это не «попробуйте позже».
            log.error("Groq отверг ключ: %s", e)
            await alerts.notify(msg.bot, "llm_auth", texts.alert_llm_auth(str(e)))
        else:
            log.error("LLM: %s", e)
        await msg.answer(LLM_FAILURE_TEXTS.get(type(e), texts.LLM_UNAVAILABLE))
        return False

    pid = db.save_prashna(
        msg.from_user.id,
        question,
        house,
        place.name,
        chart_text,
        answer.text,
        asc_sign=chart.asc_sign,
    )

    # Сохранили до отправки: не принял Telegram — толкование не потеряно, лежит в /chart.
    try:
        for chunk in chunks(html.escape(answer.text), CHUNK):
            await msg.answer(chunk)
        if answer.truncated:
            await msg.answer(texts.ANSWER_TRUNCATED)
        if verdict.cautioned:
            # Карта слабая, но читаемая: толкование выдано, квант списывается (§5.5).
            await msg.answer(texts.answer_caution(verdict.reason))
    except TelegramAPIError:
        log.exception("Telegram не принял толкование прашны %d", pid)
        with suppress(TelegramAPIError):
            await msg.answer(texts.delivery_failed(pid))
        return False
    ent = db.entitlement_for(msg.from_user.id)
    left_txt = texts.UNLIMITED_PLAIN if ent.unlimited else str(ent.left)
    await msg.answer(texts.prashna_footer(pid, left_txt))
    return True


@router.message()
async def fallback(msg: Message) -> None:
    await msg.answer(texts.FALLBACK)
