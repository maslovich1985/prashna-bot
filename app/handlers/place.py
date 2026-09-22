"""Место пользователя: /city, ввод города в FSM, геолокация."""

from __future__ import annotations

import html

from aiogram import F, Router
from aiogram.enums import ChatAction
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from .. import db, geo, texts
from .common import Form, location_kb, main_kb

router = Router(name="place")


@router.message(Command("city"))
async def city(msg: Message, state: FSMContext) -> None:
    arg = (msg.text or "").partition(" ")[2].strip()
    if not arg:
        await state.set_state(Form.waiting_city)
        await msg.answer(texts.ASK_CITY, reply_markup=location_kb())
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
