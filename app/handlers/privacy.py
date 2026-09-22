"""Приватность: /privacy и необратимое /delete_me (E-01, E-03)."""

from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from .. import db, texts

log = logging.getLogger(__name__)

router = Router(name="privacy")

CONFIRM = "delete_me:yes"
CANCEL = "delete_me:no"


def confirm_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=texts.DELETE_ME_CONFIRM_BUTTON, callback_data=CONFIRM)],
            [InlineKeyboardButton(text=texts.DELETE_ME_CANCEL_BUTTON, callback_data=CANCEL)],
        ]
    )


@router.message(Command("privacy"))
async def privacy(msg: Message) -> None:
    await msg.answer(texts.PRIVACY)


@router.message(Command("delete_me"))
async def delete_me(msg: Message) -> None:
    ent = db.entitlement_for(msg.from_user.id)
    await msg.answer(texts.delete_me_warning(ent), reply_markup=confirm_kb())


@router.callback_query(F.data == CANCEL)
async def delete_me_cancelled(call: CallbackQuery) -> None:
    await call.answer()
    if call.message is not None:
        await call.message.edit_text(texts.DELETE_ME_CANCELLED)


@router.callback_query(F.data == CONFIRM)
async def delete_me_confirmed(call: CallbackQuery) -> None:
    deleted = db.delete_user_data(call.from_user.id)
    log.info("Удалены данные пользователя %s: %s", call.from_user.id, deleted)
    await call.answer()
    if call.message is not None:
        await call.message.edit_text(texts.delete_me_done(deleted["prashna"]))
