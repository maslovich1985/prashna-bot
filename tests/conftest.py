"""Общие фикстуры. БД для каждого теста — отдельный файл в tmp_path."""

from __future__ import annotations

import dataclasses
import itertools
from collections.abc import Iterator
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.base import BaseSession
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    CallbackQuery,
    Chat,
    Location,
    Message,
    PreCheckoutQuery,
    SuccessfulPayment,
    Update,
    User,
)

from app import db as db_module
from app.config import settings
from app.handlers import ROUTERS, register


@pytest.fixture(autouse=True)
def db_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    # app/config.py читает env на импорте, поэтому monkeypatch.setenv("DB_PATH") уже
    # ни на что не влияет: подменяем сам объект настроек в модуле, который его использует.
    path = tmp_path / "test.sqlite3"
    monkeypatch.setattr(db_module, "settings", dataclasses.replace(settings, db_path=path))
    db_module.init()
    yield path


@dataclasses.dataclass
class Sent:
    """Один исходящий вызов Bot API, перехваченный стабом сессии."""

    method: str
    data: dict[str, Any]

    @property
    def text(self) -> str:
        return self.data.get("text") or self.data.get("caption") or ""


class FakeSession(BaseSession):
    """Сессия без сети: складывает вызовы в список и возвращает правдоподобный ответ.

    `aiogram-tests` намеренно не берём: пакет отстаёт от версий aiogram и ломается
    на aiogram>=3.31.
    """

    def __init__(self) -> None:
        super().__init__()
        self.sent: list[Sent] = []

    async def close(self) -> None:
        return None

    async def make_request(
        self,
        bot: Bot,
        method: Any,
        timeout: int | None = None,  # noqa: ASYNC109 — сигнатуру задаёт BaseSession
    ) -> Any:
        self.sent.append(Sent(type(method).__name__, method.model_dump(exclude_none=True)))
        return self._result_for(method)

    async def stream_content(self, *args: Any, **kwargs: Any) -> Any:  # pragma: no cover
        raise NotImplementedError

    @staticmethod
    def _result_for(method: Any) -> Any:
        name = type(method).__name__
        if name in ("SendMessage", "SendDocument"):
            return Message(
                message_id=1,
                date=datetime.now(timezone.utc),
                chat=Chat(id=getattr(method, "chat_id", CHAT_ID), type="private"),
            )
        if name == "GetMe":
            return User(id=BOT_ID, is_bot=True, first_name="test", username="test_bot")
        return True

    @property
    def texts(self) -> list[str]:
        return [s.text for s in self.sent if s.text]


BOT_ID = 1234567
CHAT_ID = 777
USER_ID = 777


@pytest.fixture
def user_id() -> int:
    return USER_ID


@pytest.fixture
def session() -> FakeSession:
    return FakeSession()


@pytest.fixture
def bot(session: FakeSession) -> Bot:
    return Bot(
        token=f"{BOT_ID}:TEST",
        session=session,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )


@pytest.fixture(autouse=True)
def detached_routers() -> Iterator[None]:
    # Роутеры в app.handlers — модульные синглтоны, а aiogram запрещает подключать
    # один роутер ко второму диспетчеру. Между тестами отцепляем их вручную.
    yield
    for router in ROUTERS:
        router._parent_router = None


@pytest.fixture
def dp() -> Dispatcher:
    dispatcher = Dispatcher(storage=MemoryStorage())
    register(dispatcher)
    return dispatcher


def make_message(
    text: str | None = None,
    user_id: int = USER_ID,
    location: Location | None = None,
    successful_payment: SuccessfulPayment | None = None,
) -> Message:
    return Message(
        message_id=next(_message_ids),
        date=datetime.now(timezone.utc),
        chat=Chat(id=user_id, type="private"),
        from_user=User(id=user_id, is_bot=False, first_name="Тест", username="tester"),
        text=text,
        location=location,
        successful_payment=successful_payment,
    )


_message_ids = itertools.count(100)


@pytest.fixture
def feed(bot: Bot, dp: Dispatcher, session: FakeSession):
    """Прогоняет сообщение через диспетчер и отдаёт перехваченные ответы."""

    async def _feed(
        text: str | None = None, user_id: int = USER_ID, location: Location | None = None
    ) -> list[Sent]:
        before = len(session.sent)
        update = Update(
            update_id=next(_message_ids),
            message=make_message(text, user_id=user_id, location=location),
        )
        await dp.feed_update(bot, update)
        return session.sent[before:]

    return _feed


@pytest.fixture
def feed_callback(bot: Bot, dp: Dispatcher, session: FakeSession):
    """То же для нажатия инлайн-кнопки: апдейт с callback_query."""

    async def _feed(data: str, user_id: int = USER_ID) -> list[Sent]:
        before = len(session.sent)
        update = Update(
            update_id=next(_message_ids),
            callback_query=CallbackQuery(
                id=str(next(_message_ids)),
                from_user=User(id=user_id, is_bot=False, first_name="Тест", username="tester"),
                chat_instance="test",
                message=make_message(user_id=user_id),
                data=data,
            ),
        )
        await dp.feed_update(bot, update)
        return session.sent[before:]

    return _feed


@pytest.fixture
def feed_pre_checkout(bot: Bot, dp: Dispatcher, session: FakeSession):
    """Апдейт pre_checkout_query: Telegram ждёт ответа не дольше 10 секунд."""

    async def _feed(
        payload: str, total_amount: int, currency: str = "XTR", user_id: int = USER_ID
    ) -> list[Sent]:
        before = len(session.sent)
        update = Update(
            update_id=next(_message_ids),
            pre_checkout_query=PreCheckoutQuery(
                id=str(next(_message_ids)),
                from_user=User(id=user_id, is_bot=False, first_name="Тест", username="tester"),
                currency=currency,
                total_amount=total_amount,
                invoice_payload=payload,
            ),
        )
        await dp.feed_update(bot, update)
        return session.sent[before:]

    return _feed


@pytest.fixture
def feed_payment(bot: Bot, dp: Dispatcher, session: FakeSession):
    """Апдейт successful_payment: деньги уже списаны, доступ ещё не выдан."""

    async def _feed(
        payload: str,
        charge_id: str,
        total_amount: int,
        currency: str = "XTR",
        user_id: int = USER_ID,
    ) -> list[Sent]:
        before = len(session.sent)
        payment = SuccessfulPayment(
            currency=currency,
            total_amount=total_amount,
            invoice_payload=payload,
            telegram_payment_charge_id=charge_id,
            provider_payment_charge_id=charge_id,
        )
        update = Update(
            update_id=next(_message_ids),
            message=make_message(user_id=user_id, successful_payment=payment),
        )
        await dp.feed_update(bot, update)
        return session.sent[before:]

    return _feed


@pytest.fixture
def with_place(user_id: int) -> None:
    """Сохранённое место. Без него прашна-путь отвечает «укажите место» (F-07)."""
    db_module.upsert_user(user_id, "tester", place="Томск", lat=56.5, lon=84.97, tz="Asia/Tomsk")
