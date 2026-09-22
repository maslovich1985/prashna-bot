"""Перебор прокси и таймауты (H-01)."""

from __future__ import annotations

import dataclasses
from typing import Any

import httpx
import pytest

from app import llm
from app.config import settings

ANSWER = {
    "choices": [
        {"message": {"content": "Вердикт: да. " + "Обоснование. " * 30}, "finish_reason": "stop"}
    ]
}


@pytest.fixture
def two_proxies(monkeypatch: pytest.MonkeyPatch) -> tuple[str, str]:
    first, second = "http://first:8080", "http://second:8080"
    monkeypatch.setattr(
        llm, "settings", dataclasses.replace(settings, llm_proxy=first, llm_proxy_fallback=second)
    )
    return first, second


def _record(monkeypatch: pytest.MonkeyPatch, behaviour) -> list[str | None]:
    """Подменяет транспорт: запоминает, через какой прокси шёл каждый запрос."""
    seen: list[str | None] = []
    real_init = httpx.AsyncClient.__init__

    def fake_init(self, *args: Any, **kwargs: Any) -> None:
        self._test_proxy = kwargs.get("proxy")
        self._test_timeout = kwargs.get("timeout")
        real_init(self, *args, **{k: v for k, v in kwargs.items() if k != "proxy"})

    async def fake_post(self, url: str, **kwargs: Any) -> httpx.Response:
        seen.append(self._test_proxy)
        return behaviour(self._test_proxy)

    monkeypatch.setattr(httpx.AsyncClient, "__init__", fake_init)
    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)
    return seen


def test_chain_is_empty_without_proxies(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        llm, "settings", dataclasses.replace(settings, llm_proxy="", llm_proxy_fallback="")
    )
    assert llm.proxy_chain() == [None]


def test_chain_keeps_the_order(two_proxies: tuple[str, str]) -> None:
    assert llm.proxy_chain() == list(two_proxies)


def test_proxy_timeout_is_shorter_than_groq(two_proxies: tuple[str, str]) -> None:
    # Иначе первый адрес съест всё окно и переключаться будет некуда.
    assert llm.timeout_for(two_proxies[0]) < settings.groq_timeout
    assert llm.timeout_for(None) == settings.groq_timeout


async def test_second_proxy_is_used_when_the_first_is_down(
    two_proxies: tuple[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    first, second = two_proxies

    def behaviour(proxy: str | None) -> httpx.Response:
        if proxy == first:
            raise httpx.ConnectError("прокси лежит")
        return httpx.Response(200, json=ANSWER, request=httpx.Request("POST", "http://x"))

    seen = _record(monkeypatch, behaviour)
    answer = await llm.interpret("Вопрос?", 10, "работа", "КАРТА", ["фактор"])

    assert answer.text.startswith("Вердикт")
    assert seen == [first, second]


async def test_both_down_reports_unavailable(
    two_proxies: tuple[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    def behaviour(proxy: str | None) -> httpx.Response:
        raise httpx.ConnectError("оба лежат")

    seen = _record(monkeypatch, behaviour)
    with pytest.raises(llm.LLMUnavailable):
        await llm.interpret("Вопрос?", 10, "работа", "КАРТА", ["фактор"])
    # Три попытки разошлись по двум адресам, а не били в один.
    assert set(seen) == set(two_proxies)


async def test_single_proxy_still_retries(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        llm,
        "settings",
        dataclasses.replace(settings, llm_proxy="http://only:8080", llm_proxy_fallback=""),
    )
    monkeypatch.setattr(llm.asyncio, "sleep", _no_sleep)

    def behaviour(proxy: str | None) -> httpx.Response:
        raise httpx.ConnectError("лежит")

    seen = _record(monkeypatch, behaviour)
    with pytest.raises(llm.LLMUnavailable):
        await llm.interpret("Вопрос?", 10, "работа", "КАРТА", ["фактор"])
    assert seen == ["http://only:8080"] * 3


async def _no_sleep(*args: Any, **kwargs: Any) -> None:
    return None
