"""Счётчики сбоев и health-пинг: видно, кто виноват — прокси или Groq (H-02)."""

from __future__ import annotations

import dataclasses
from typing import Any

import httpx
import pytest

from app import llm, observability
from app.config import settings

PROXY = "http://proxy:8080"


@pytest.fixture(autouse=True)
def clean_counters() -> None:
    llm.reset_failures()


def _response(status: int) -> httpx.Response:
    return httpx.Response(status, request=httpx.Request("POST", "http://x"))


@pytest.mark.parametrize(
    ("error", "proxy", "expected"),
    [
        (httpx.ProxyError("нет"), PROXY, (llm.PROXY, llm.CONNECT)),
        (httpx.ConnectTimeout("нет"), PROXY, (llm.PROXY, llm.TIMEOUT)),
        (httpx.ConnectTimeout("нет"), None, (llm.GROQ, llm.TIMEOUT)),
        (httpx.ConnectError("нет"), None, (llm.GROQ, llm.CONNECT)),
    ],
)
def test_blame_goes_to_the_right_side(error: Exception, proxy: str | None, expected) -> None:
    assert llm.classify(error, proxy) == expected


def test_server_error_is_always_groq() -> None:
    # Ответ дошёл — значит, прокси отработал, а ругается Groq.
    err = httpx.HTTPStatusError("500", request=_response(500).request, response=_response(500))
    assert llm.classify(err, PROXY) == (llm.GROQ, llm.SERVER)


def test_counters_accumulate() -> None:
    llm.note_failure(llm.PROXY, llm.TIMEOUT)
    llm.note_failure(llm.PROXY, llm.TIMEOUT)
    llm.note_failure(llm.GROQ, llm.RATE_LIMIT)
    assert llm.failures() == {"прокси/таймаут": 2, "groq/429": 1}


def test_tagging_without_sentry_is_a_noop(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(observability, "settings", dataclasses.replace(settings, sentry_dsn=""))

    def _boom(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("тег ушёл в выключенный Sentry")

    monkeypatch.setattr(observability.sentry_sdk, "set_tag", _boom)
    llm.note_failure(llm.GROQ, llm.SERVER)
    assert llm.failures() == {"groq/5xx": 1}


def test_failed_request_is_counted(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        llm, "settings", dataclasses.replace(settings, llm_proxy=PROXY, llm_proxy_fallback="")
    )

    async def _no_sleep(*args: Any, **kwargs: Any) -> None:
        return None

    async def failing_post(self, url: str, **kwargs: Any) -> httpx.Response:
        raise httpx.ProxyError("лежит")

    monkeypatch.setattr(llm.asyncio, "sleep", _no_sleep)
    monkeypatch.setattr(httpx.AsyncClient, "post", failing_post)

    import asyncio

    with pytest.raises(llm.LLMUnavailable):
        asyncio.run(llm.interpret("Вопрос?", 10, "работа", "КАРТА", ["фактор"]))
    assert llm.failures() == {"прокси/соединение": 3}


async def test_health_check_reports_a_live_channel(monkeypatch: pytest.MonkeyPatch) -> None:
    async def ok_get(self, url: str, **kwargs: Any) -> httpx.Response:
        return httpx.Response(200, request=httpx.Request("GET", url))

    monkeypatch.setattr(httpx.AsyncClient, "get", ok_get)
    assert await llm.health_check() is True


async def test_health_check_never_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    """Бота не валим: карта считается и без толкования."""

    async def dead_get(self, url: str, **kwargs: Any) -> httpx.Response:
        raise httpx.ConnectError("канала нет")

    monkeypatch.setattr(httpx.AsyncClient, "get", dead_get)
    assert await llm.health_check() is False
    assert llm.failures()


async def test_health_check_tries_the_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    second = "http://second:8080"
    monkeypatch.setattr(
        llm, "settings", dataclasses.replace(settings, llm_proxy=PROXY, llm_proxy_fallback=second)
    )
    seen: list[str | None] = []
    real_init = httpx.AsyncClient.__init__

    def fake_init(self, *args: Any, **kwargs: Any) -> None:
        self._test_proxy = kwargs.get("proxy")
        real_init(self, *args, **{k: v for k, v in kwargs.items() if k != "proxy"})

    async def get(self, url: str, **kwargs: Any) -> httpx.Response:
        seen.append(self._test_proxy)
        if self._test_proxy == PROXY:
            raise httpx.ProxyError("первый лежит")
        return httpx.Response(200, request=httpx.Request("GET", url))

    monkeypatch.setattr(httpx.AsyncClient, "__init__", fake_init)
    monkeypatch.setattr(httpx.AsyncClient, "get", get)
    assert await llm.health_check() is True
    assert seen == [PROXY, second]
