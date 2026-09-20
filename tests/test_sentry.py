"""Sentry: теги события есть, текста вопроса в них нет."""

from __future__ import annotations

import dataclasses
from typing import Any

import pytest
import sentry_sdk
from aiogram.types import Location, Update
from conftest import USER_ID, make_message

from app import observability
from app.config import settings

QUESTION = "Получу ли я эту работу в этом году?"


class FakeScope:
    def __init__(self) -> None:
        self.tags: dict[str, Any] = {}

    def set_tag(self, key: str, value: Any) -> None:
        self.tags[key] = value

    def __enter__(self) -> FakeScope:
        return self

    def __exit__(self, *exc: object) -> None:
        return None


@pytest.fixture
def scope(monkeypatch: pytest.MonkeyPatch) -> FakeScope:
    fake = FakeScope()
    monkeypatch.setattr(sentry_sdk, "isolation_scope", lambda: fake)
    return fake


async def run_middleware(update: Update) -> None:
    async def handler(event: Update, data: dict[str, Any]) -> None:
        return None

    data = {"event_from_user": update.message.from_user}
    await observability.SentryMiddleware()(handler, update, data)


def update_with(**kwargs: Any) -> Update:
    return Update(update_id=1, message=make_message(**kwargs))


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("/city Томск", "/city"),
        ("/city@prashna_bot", "/city"),
        ("/start", "/start"),
        (QUESTION, observability.PRASHNA_EVENT),
        (None, observability.UNKNOWN_EVENT),
    ],
)
def test_event_name(text: str | None, expected: str) -> None:
    assert observability.event_name(make_message(text)) == expected


def test_event_name_for_location() -> None:
    message = make_message(location=Location(latitude=56.5, longitude=84.97))
    assert observability.event_name(message) == "location"


def test_event_name_without_message() -> None:
    assert observability.event_name(None) == observability.UNKNOWN_EVENT


async def test_middleware_tags_user_and_command(scope: FakeScope) -> None:
    await run_middleware(update_with(text="/city Томск"))
    assert scope.tags == {"user_id": USER_ID, "command": "/city"}


async def test_middleware_does_not_leak_question_text(scope: FakeScope) -> None:
    await run_middleware(update_with(text=QUESTION))
    assert scope.tags["command"] == observability.PRASHNA_EVENT
    assert QUESTION not in str(scope.tags)


def test_init_disabled_without_dsn(monkeypatch: pytest.MonkeyPatch) -> None:
    called: list[dict[str, Any]] = []
    monkeypatch.setattr(sentry_sdk, "init", lambda **kw: called.append(kw))
    monkeypatch.setattr(observability, "settings", dataclasses.replace(settings, sentry_dsn=""))
    assert observability.init() is False
    assert called == []


def test_init_never_sends_pii(monkeypatch: pytest.MonkeyPatch) -> None:
    called: list[dict[str, Any]] = []
    monkeypatch.setattr(sentry_sdk, "init", lambda **kw: called.append(kw))
    monkeypatch.setattr(
        observability,
        "settings",
        dataclasses.replace(settings, sentry_dsn="https://x@example.invalid/1", sentry_env="test"),
    )
    assert observability.init() is True
    assert called[0]["send_default_pii"] is False
    assert called[0]["traces_sample_rate"] == 0.0
    assert called[0]["environment"] == "test"
