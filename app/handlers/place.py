"""Место пользователя: /city, ввод города в FSM, геолокация."""

from __future__ import annotations

import html

from aiogram import F, Router
from aiogram.enums import ChatAction
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from .. import db, geo, texts
from ..constants import CITIES
from .common import Form, location_kb, main_kb

router = Router(name="place")

CITY_PREFIX = "city:"
CITY_OTHER = "city:other"


def cities_kb() -> InlineKeyboardMarkup:
    """Быстрый выбор без геокодера. Два столбца: десять городов читаются одним экраном."""
    keys = list(CITIES)
    rows = [
        [
            InlineKeyboardButton(text=CITIES[key].name, callback_data=f"{CITY_PREFIX}{key}")
            for key in keys[i : i + 2]
        ]
        for i in range(0, len(keys), 2)
    ]
    rows.append([InlineKeyboardButton(text=texts.CITY_OTHER_BUTTON, callback_data=CITY_OTHER)])
    return InlineKeyboardMarkup(inline_keyboard=rows)


@router.message(Command("city"))
async def city(msg: Message, state: FSMContext) -> None:
    arg = (msg.text or "").partition(" ")[2].strip()
    if not arg:
        # Кнопки не отменяют ввод руками: FSM по-прежнему ждёт название города,
        # выбор из списка просто снимает поход в Nominatim.
        await state.set_state(Form.waiting_city)
        await msg.answer(texts.ASK_CITY, reply_markup=location_kb())
        await msg.answer(texts.PICK_CITY, reply_markup=cities_kb())
        return
    await set_city(msg, arg, state)


@router.message(Form.waiting_city, F.text)
async def city_input(msg: Message, state: FSMContext) -> None:
    await set_city(msg, msg.text.strip(), state)


async def set_city(msg: Message, query: str, state: FSMContext) -> None:
    await msg.bot.send_chat_action(msg.chat.id, ChatAction.TYPING)
    place = await geo.geocode(query)
    if not place:
        await msg.answer(texts.CITY_NOT_FOUND)
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
        texts.city_set(html.escape(place.name), place.lat, place.lon, place.tz),
        reply_markup=main_kb(),
    )


@router.message(F.location)
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
    await msg.answer(texts.location_set(place.lat, place.lon, place.tz), reply_markup=main_kb())


@router.callback_query(F.data == CITY_OTHER)
async def city_other(call: CallbackQuery, state: FSMContext) -> None:
    await call.answer()
    await state.set_state(Form.waiting_city)
    if call.message is not None:
        await call.message.answer(texts.ASK_CITY)


@router.callback_query(F.data.startswith(CITY_PREFIX))
async def city_chosen(call: CallbackQuery, state: FSMContext) -> None:
    city = CITIES.get((call.data or "").removeprefix(CITY_PREFIX))
    if city is None or call.message is None:
        # Кнопка из старого сообщения, список городов с тех пор поменяли.
        await call.answer(texts.CITY_GONE, show_alert=True)
        return
    db.upsert_user(
        call.from_user.id,
        call.from_user.username,
        place=city.name,
        lat=city.lat,
        lon=city.lon,
        tz=city.tz,
    )
    await state.clear()
    await call.answer()
    await call.message.edit_text(texts.city_set(city.name, city.lat, city.lon, city.tz))
