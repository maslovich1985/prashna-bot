"""Разбор ответа Groq и классификация сбоев — таблица §5.4.1."""

from __future__ import annotations

import httpx
import pytest

from app import llm


def _payload(text: str, finish_reason: str = "stop") -> dict:
    return {"choices": [{"message": {"content": text}, "finish_reason": finish_reason}]}


def test_full_answer_is_not_truncated() -> None:
    answer = llm._parse(_payload("Вердикт: да. " + "Обоснование. " * 40))
    assert answer.truncated is False
    assert answer.text.startswith("Вердикт")


def test_answer_cut_by_max_tokens_is_marked_truncated() -> None:
    answer = llm._parse(_payload("Вердикт: да. " + "Обоснование. " * 40, finish_reason="length"))
    assert answer.truncated is True


@pytest.mark.parametrize("text", ["", "   ", "Вердикт: да."])
def test_empty_or_short_answer_is_not_an_interpretation(text: str) -> None:
    with pytest.raises(llm.LLMEmptyAnswer):
        llm._parse(_payload(text))


def test_none_content_does_not_crash() -> None:
    with pytest.raises(llm.LLMEmptyAnswer):
        llm._parse({"choices": [{"message": {"content": None}, "finish_reason": "stop"}]})


def test_every_failure_is_an_llm_error() -> None:
    # Хэндлер ловит один LLMError: подкласс мимо него ушёл бы в неучтённое исключение.
    for cls in (
        llm.LLMUnavailable,
        llm.LLMRateLimited,
        llm.LLMAuthError,
        llm.LLMTimeout,
        llm.LLMEmptyAnswer,
    ):
        assert issubclass(cls, llm.LLMError)


class _Response:
    def __init__(self, status_code: int, payload: dict | None = None) -> None:
        self.status_code = status_code
        self.headers = {"retry-after": "0"}
        self._payload = payload or {}

    def json(self) -> dict:
        return self._payload

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("ошибка", request=None, response=None)  # type: ignore[arg-type]


def _client_returning(*results: object):
    """Подменяет httpx.AsyncClient: каждый вызов post отдаёт следующий результат."""
    queue = list(results)

    class _Client:
        async def __aenter__(self) -> _Client:
            return self

        async def __aexit__(self, *exc: object) -> None:
            return None

        async def post(self, *args: object, **kwargs: object) -> _Response:
            item = queue.pop(0) if len(queue) > 1 else queue[0]
            if isinstance(item, Exception):
                raise item
            return item

    return lambda *args, **kwargs: _Client()


@pytest.fixture
def no_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _instant(_seconds: float) -> None:
        return None

    monkeypatch.setattr(llm.asyncio, "sleep", _instant)


async def _interpret() -> llm.Answer:
    return await llm.interpret("Вопрос?", 10, "карьера", "КАРТА", ["фактор"], retries=2)


@pytest.mark.parametrize("status", [401, 403])
async def test_auth_error_is_not_retried(
    status: int, monkeypatch: pytest.MonkeyPatch, no_sleep
) -> None:
    calls = 0

    def factory(*args: object, **kwargs: object):
        nonlocal calls
        calls += 1
        return _client_returning(_Response(status))()

    monkeypatch.setattr(llm.httpx, "AsyncClient", factory)
    with pytest.raises(llm.LLMAuthError):
        await _interpret()
    assert calls == 1  # ретраи протухший ключ не чинят


async def test_rate_limit_survives_all_retries(monkeypatch: pytest.MonkeyPatch, no_sleep) -> None:
    monkeypatch.setattr(llm.httpx, "AsyncClient", _client_returning(_Response(429)))
    with pytest.raises(llm.LLMRateLimited):
        await _interpret()


async def test_timeout(monkeypatch: pytest.MonkeyPatch, no_sleep) -> None:
    monkeypatch.setattr(
        llm.httpx, "AsyncClient", _client_returning(httpx.TimeoutException("вышло время"))
    )
    with pytest.raises(llm.LLMTimeout):
        await _interpret()


async def test_server_error_is_unavailable(monkeypatch: pytest.MonkeyPatch, no_sleep) -> None:
    monkeypatch.setattr(llm.httpx, "AsyncClient", _client_returning(_Response(503)))
    with pytest.raises(llm.LLMUnavailable):
        await _interpret()


async def test_retry_then_success(monkeypatch: pytest.MonkeyPatch, no_sleep) -> None:
    good = _Response(200, _payload("Вердикт: да. " + "Обоснование. " * 40))
    monkeypatch.setattr(llm.httpx, "AsyncClient", _client_returning(_Response(503), good))
    assert (await _interpret()).truncated is False
