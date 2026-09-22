"""Общее для нескольких групп хэндлеров: состояние FSM, клавиатура, место пользователя."""

from __future__ import annotations

from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
)

from .. import db, geo, texts
from .billing import BUY_CALLBACK


class Form(StatesGroup):
    waiting_city = State()


def main_kb() -> ReplyKeyboardMarkup:
    """Постоянная навигация. Держится между сообщениями: `one_time_keyboard` здесь
    оставил бы пользователя с пустым экраном после первого же ответа."""
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=texts.BTN_ASK), KeyboardButton(text=texts.BTN_BALANCE)],
            [KeyboardButton(text=texts.BTN_HISTORY), KeyboardButton(text=texts.BTN_HELP)],
        ],
        resize_keyboard=True,
        is_persistent=True,
    )


def location_kb() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text=texts.LOCATION_BUTTON, request_location=True)]],
        resize_keyboard=True,
        one_time_keyboard=True,
    )


async def place_for(user_id: int) -> geo.Place | None:
    """Сохранённое место или `None`. Подставлять умолчание здесь нельзя: место
    определяет лагну, то есть ответ, и молчаливая Москва — это брак расчёта."""
    u = db.get_user(user_id)
    if u and u.get("lat") is not None and u.get("lon") is not None:
        return geo.Place(u.get("place") or "—", u["lat"], u["lon"], u.get("tz") or "UTC")
    return None


def buy_kb() -> InlineKeyboardMarkup:
    """Кнопку обрабатывает billing: продажами владеет он, экраны только зовут."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=texts.BALANCE_BUY_BUTTON, callback_data=BUY_CALLBACK)]
        ]
    )
