"""Telegram-бот прашна-гороскопа (aiogram 3)."""

from __future__ import annotations

import asyncio
import html
import logging
from datetime import datetime, timezone

from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ChatAction, ParseMode
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    BufferedInputFile,
    KeyboardButton,
    Message,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
)

from . import db, geo, llm
from .astro import constants as C
from .astro.chart import build_chart
from .astro.prashna import detect_house, judgment_factors, render_chart_text, render_short
from .config import settings

log = logging.getLogger(__name__)
router_storage = MemoryStorage()

WELCOME = (
    "🕉 <b>Прашна-бот</b> — ведическая хорарная астрология.\n\n"
    "Прашна — это карта на <i>момент вопроса</i>. Ответ даёт не бот, а положение планет "
    "в ту секунду, когда вопрос был искренне задан.\n\n"
    "<b>Как задать вопрос</b>\n"
    "1. Укажите место, откуда спрашиваете: /city Томск (или пришлите геолокацию).\n"
    "2. Сосредоточьтесь на вопросе и просто напишите его одним сообщением.\n\n"
    "<b>Правила прашны</b>\n"
    "• один вопрос за раз, конкретный и вызревший;\n"
    "• не задавайте один и тот же вопрос повторно ради «лучшего» ответа;\n"
    "• формулируйте так, чтобы ответом могло быть «да» или «нет».\n\n"
    "<b>Команды</b>\n"
    "/city &lt;город&gt; — задать место\n"
    "/me — мои настройки и остаток лимита\n"
    "/history — последние вопросы\n"
    "/chart &lt;id&gt; — карта и ответ по номеру из истории\n"
    "/forget — очистить историю\n"
    "/help — справка\n\n"
    "<i>Толкование носит рекомендательный характер и не заменяет консультацию врача, "
    "юриста или финансового специалиста.</i>"
)


class Form(StatesGroup):
    waiting_city = State()


def _location_kb() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="📍 Отправить геолокацию", request_location=True)]],
        resize_keyboard=True,
        one_time_keyboard=True,
    )


async def _place_for(user_id: int) -> geo.Place:
    u = db.get_user(user_id)
    if u and u.get("lat") is not None and u.get("lon") is not None:
        return geo.Place(u.get("place") or "—", u["lat"], u["lon"], u.get("tz") or "UTC")
    return geo.default_place()


def register(dp: Dispatcher) -> None:

    @dp.message(CommandStart())
    async def start(msg: Message, state: FSMContext) -> None:
        await state.clear()
        db.upsert_user(msg.from_user.id, msg.from_user.username)
        await msg.answer(WELCOME, reply_markup=_location_kb())

    @dp.message(Command("help"))
    async def help_cmd(msg: Message) -> None:
        await msg.answer(WELCOME)

    @dp.message(Command("cancel"))
    async def cancel(msg: Message, state: FSMContext) -> None:
        await state.clear()
        await msg.answer("Отменено.", reply_markup=ReplyKeyboardRemove())

    # ------------------------------- место -------------------------------- #

    @dp.message(Command("city"))
    async def city(msg: Message, state: FSMContext) -> None:
        arg = (msg.text or "").partition(" ")[2].strip()
        if not arg:
            await state.set_state(Form.waiting_city)
            await msg.answer(
                "Напишите город, из которого вы задаёте вопрос "
                "(например: <code>Москва</code>), или пришлите геолокацию.",
                reply_markup=_location_kb(),
            )
            return
        await _set_city(msg, arg, state)

    @dp.message(Form.waiting_city, F.text)
    async def city_input(msg: Message, state: FSMContext) -> None:
        await _set_city(msg, msg.text.strip(), state)

    async def _set_city(msg: Message, query: str, state: FSMContext) -> None:
        await msg.bot.send_chat_action(msg.chat.id, ChatAction.TYPING)
        place = await geo.geocode(query)
        if not place:
            await msg.answer(
                "Не нашёл такой город. Попробуйте иначе "
                "(например «Нижний Новгород, Россия») или пришлите геолокацию."
            )
            return
        db.upsert_user(
            msg.from_user.id,
            msg.from_user.username,
            place=place.name,
            lat=place.lat,
            lon=place.lon,
            tz=place.tz,
        )
        await state.clear()
        await msg.answer(
            f"Место установлено: <b>{html.escape(place.name)}</b>\n"
            f"Координаты: {place.lat:.4f}, {place.lon:.4f}\nЧасовой пояс: {place.tz}\n\n"
            "Теперь просто напишите свой вопрос одним сообщением.",
            reply_markup=ReplyKeyboardRemove(),
        )

    @dp.message(F.location)
    async def location(msg: Message, state: FSMContext) -> None:
        place = geo.place_from_coords(msg.location.latitude, msg.location.longitude)
        db.upsert_user(
            msg.from_user.id,
            msg.from_user.username,
            place=place.name,
            lat=place.lat,
            lon=place.lon,
            tz=place.tz,
        )
        await state.clear()
        await msg.answer(
            f"Место установлено по геолокации: {place.lat:.4f}, {place.lon:.4f} "
            f"({place.tz}).\n\nНапишите свой вопрос.",
            reply_markup=ReplyKeyboardRemove(),
        )

    # ------------------------------ сервис -------------------------------- #

    @dp.message(Command("me"))
    async def me(msg: Message) -> None:
        is_admin = msg.from_user.id in settings.admin_ids
        place = await _place_for(msg.from_user.id)
        if is_admin:
            left = "∞ <b>безлимит</b>"
        else:
            left = db.remaining(msg.from_user.id, settings.daily_limit)
        await msg.answer(
            f"Место: <b>{html.escape(place.name)}</b> "
            f"({place.lat:.3f}, {place.lon:.3f}, {place.tz})\n"
            f"Осталось прашн сегодня: {left}\n"
            f"Аянамша: {settings.ayanamsa}"
        )

    @dp.message(Command("history"))
    async def history(msg: Message) -> None:
        rows = db.history(msg.from_user.id, 10)
        if not rows:
            await msg.answer("История пуста.")
            return
        lines = ["<b>Последние вопросы:</b>"]
        for r in rows:
            when = datetime.fromisoformat(r["asked_at"]).strftime("%d.%m.%Y %H:%M")
            q = html.escape(r["question"][:80])
            lines.append(f"#{r['id']} · {when} · дом {r['house']}\n   {q}")
        lines.append("\nКарта и ответ: /chart &lt;номер&gt;")
        await msg.answer("\n".join(lines))

    @dp.message(Command("chart"))
    async def chart_cmd(msg: Message) -> None:
        arg = (msg.text or "").partition(" ")[2].strip()
        if not arg.isdigit():
            await msg.answer("Укажите номер из /history, например: <code>/chart 12</code>")
            return
        row = db.get_prashna(msg.from_user.id, int(arg))
        if not row:
            await msg.answer("Такой прашны в вашей истории нет.")
            return
        doc = BufferedInputFile(
            (
                f"Вопрос: {row['question']}\n\n{row['chart_text']}\n\nТОЛКОВАНИЕ:\n{row['answer']}"
            ).encode(),
            filename=f"prashna_{row['id']}.txt",
        )
        await msg.answer_document(doc, caption=f"Прашна #{row['id']}")

    @dp.message(Command("forget"))
    async def forget(msg: Message) -> None:
        n = db.clear_history(msg.from_user.id)
        await msg.answer(f"Удалено записей: {n}.")

    @dp.message(Command("stats"))
    async def stats(msg: Message) -> None:
        if msg.from_user.id not in settings.admin_ids:
            return
        s = db.stats()
        await msg.answer("\n".join(f"{k}: {v}" for k, v in s.items()))

    # ------------------------------ прашна -------------------------------- #

    @dp.message(F.text & ~F.text.startswith("/"))
    async def prashna(msg: Message, state: FSMContext) -> None:
        question = msg.text.strip()
        if len(question) < 8:
            await msg.answer("Сформулируйте вопрос подробнее — прашна требует ясной формулировки.")
            return
        if len(question) > 500:
            await msg.answer(
                "Вопрос слишком длинный. Уложитесь в 500 символов и задайте одну тему."
            )
            return

        if msg.from_user.id not in settings.admin_ids:
            ok, reason = db.check_and_bump(
                msg.from_user.id, settings.daily_limit, settings.cooldown_seconds
            )
            if not ok:
                await msg.answer(reason)
                return

        db.upsert_user(msg.from_user.id, msg.from_user.username)
        place = await _place_for(msg.from_user.id)
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
            await msg.answer("Не удалось рассчитать карту. Проверьте настройки места (/city).")
            return

        await msg.answer(
            f"🕉 <b>Прашна принята</b>\n"
            f"<i>{html.escape(question)}</i>\n\n"
            f"Момент: {chart.when_local.strftime('%d.%m.%Y %H:%M:%S')} ({place.tz}), "
            f"{html.escape(place.name)}\n\n" + render_short(chart)
        )

        await msg.bot.send_chat_action(msg.chat.id, ChatAction.TYPING)
        try:
            answer = await llm.interpret(
                question, house, C.HOUSE_MEANINGS[house], chart_text, factors
            )
        except llm.LLMError as e:
            log.error("LLM: %s", e)
            await msg.answer(
                "Карта рассчитана, но сервис толкования сейчас недоступен. "
                "Попробуйте повторить через несколько минут — карта будет новой, "
                "так как прашна строится на момент вопроса."
            )
            return

        pid = db.save_prashna(msg.from_user.id, question, house, place.name, chart_text, answer)

        text = html.escape(answer)
        for chunk in _chunks(text, 3800):
            await msg.answer(chunk)
        if msg.from_user.id in settings.admin_ids:
            left_txt = "∞ безлимит"
        else:
            left_txt = str(db.remaining(msg.from_user.id, settings.daily_limit))
        await msg.answer(f"Полная карта: <code>/chart {pid}</code> · осталось сегодня: {left_txt}")

    @dp.message()
    async def fallback(msg: Message) -> None:
        await msg.answer("Пришлите вопрос текстом или воспользуйтесь /help.")


def _chunks(text: str, size: int):
    while text:
        if len(text) <= size:
            yield text
            return
        cut = text.rfind("\n", 0, size)
        if cut < size // 2:
            cut = size
        yield text[:cut]
        text = text[cut:].lstrip("\n")


async def run() -> None:
    logging.basicConfig(
        level=getattr(logging, settings.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    db.init()
    bot = Bot(settings.telegram_token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher(storage=router_storage)
    register(dp)
    me = await bot.get_me()
    log.info("Бот @%s запущен", me.username)
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
