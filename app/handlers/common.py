"""Общее для нескольких групп хэндлеров: состояние FSM, клавиатура, место пользователя."""

from __future__ import annotations

from aiogram.fsm.state import State, StatesGroup
from aiogram.types import KeyboardButton, ReplyKeyboardMarkup

from .. import db, geo, texts


class Form(StatesGroup):
    waiting_city = State()


def location_kb() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text=texts.LOCATION_BUTTON, request_location=True)]],
        resize_keyboard=True,
        one_time_keyboard=True,
    )


async def place_for(user_id: int) -> geo.Place:
    u = db.get_user(user_id)
    if u and u.get("lat") is not None and u.get("lon") is not None:
        return geo.Place(u.get("place") or "—", u["lat"], u["lon"], u.get("tz") or "UTC")
    return geo.default_place()
