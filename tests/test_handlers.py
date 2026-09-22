"""Хэндлеры целиком: настоящий Dispatcher, стаб сессии вместо Bot API.

Сеть не дёргается: геокодер и Groq замоканы, всё остальное — реальный код и SQLite.
"""

from __future__ import annotations

import dataclasses
from datetime import datetime, timezone
from typing import Any

import pytest
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import Location

from app import bot as bot_module
from app import db, geo, llm, texts
from app.astro import constants as C
from app.astro import validity
from app.config import settings
from app.handlers import basic
from app.handlers import prashna as prashna_handlers

USER_ID = 777  # совпадает с отправителем из фикстуры feed

TOMSK = geo.Place("Томск", 56.5, 84.97, "Asia/Tomsk")


@pytest.fixture(autouse=True)
def valid_prashna(monkeypatch: pytest.MonkeyPatch) -> None:
    """Валидность считается по небу на момент прогона, и примерно в 15% моментов
    карта отказная (§5.5.1). Без этой фиксации тесты пути падали бы через раз;
    сам отказ проверяется отдельными тестами, которые фикстуру перекрывают.
    """
    monkeypatch.setattr(
        prashna_handlers, "_validity_of", lambda *args, **kwargs: validity.Verdict()
    )


@pytest.fixture
def no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """Страховка: любой поход в сеть из-под теста — ошибка, а не молчаливый запрос."""

    async def _boom(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("тест полез в сеть")

    monkeypatch.setattr(geo, "geocode", _boom)
    monkeypatch.setattr(llm, "interpret", _boom)
    monkeypatch.setattr(prashna_handlers.llm, "interpret", _boom)


async def test_start_greets_and_registers_user(feed, no_network) -> None:
    sent = await feed("/start")
    assert texts.WELCOME in sent[0].text
    assert sent[0].data.get("reply_markup") is not None
    assert db.get_user(USER_ID) is not None


async def test_help_returns_welcome(feed, no_network) -> None:
    sent = await feed("/help")
    assert [s.text for s in sent] == [texts.WELCOME]


async def test_city_with_argument_sets_place(feed, monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_geocode(query: str) -> geo.Place:
        assert query == "Томск"
        return TOMSK

    monkeypatch.setattr(geo, "geocode", fake_geocode)
    sent = await feed("/city Томск")
    assert "Томск" in sent[-1].text
    user = db.get_user(USER_ID)
    assert (user["lat"], user["lon"], user["tz"]) == (TOMSK.lat, TOMSK.lon, TOMSK.tz)


async def test_city_not_found_keeps_place_unset(feed, monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_geocode(query: str) -> None:
        return None

    monkeypatch.setattr(geo, "geocode", fake_geocode)
    sent = await feed("/city Блаблабла")
    assert sent[-1].text == texts.CITY_NOT_FOUND
    assert db.get_user(USER_ID) is None


async def test_city_without_argument_asks_and_takes_next_message(
    feed, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def fake_geocode(query: str) -> geo.Place:
        return TOMSK

    monkeypatch.setattr(geo, "geocode", fake_geocode)
    asked = await feed("/city")
    assert asked[-1].text == texts.ASK_CITY
    # FSM активен: следующий текст — город, а не вопрос для прашны
    answered = await feed("Томск")
    assert "Томск" in answered[-1].text
    assert db.get_user(USER_ID)["place"] == "Томск"


async def test_location_sets_place(feed, no_network) -> None:
    sent = await feed(location=Location(latitude=56.5, longitude=84.97))
    assert "56.5000" in sent[-1].text
    assert db.get_user(USER_ID)["tz"] == "Asia/Tomsk"


async def test_me_shows_default_place_and_limit(feed, no_network) -> None:
    sent = await feed("/me")
    assert settings.default_city in sent[-1].text
    assert str(settings.daily_limit) in sent[-1].text


async def test_history_empty(feed, no_network) -> None:
    sent = await feed("/history")
    assert [s.text for s in sent] == [texts.HISTORY_EMPTY]


async def test_stats_for_non_admin_reveals_nothing(
    feed, no_network, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(basic, "settings", dataclasses.replace(settings, admin_ids=(999,)))
    sent = await feed("/stats")
    assert [s.text for s in sent] == [texts.STATS_DENIED]
    assert all(str(value) not in sent[0].text for value in db.stats().values())


async def test_stats_for_admin_shows_counters(
    feed, no_network, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(basic, "settings", dataclasses.replace(settings, admin_ids=(USER_ID,)))
    sent = await feed("/stats")
    assert sent[-1].text != texts.STATS_DENIED
    assert all(key in sent[-1].text for key in db.stats())


async def test_unknown_non_text_message_falls_back(feed, no_network) -> None:
    sent = await feed()
    assert [s.text for s in sent] == [texts.FALLBACK]


async def test_short_question_rejected_without_llm(feed, no_network) -> None:
    sent = await feed("а что")
    assert [s.text for s in sent] == [texts.QUESTION_TOO_SHORT]


async def test_long_question_rejected_without_llm(feed, no_network) -> None:
    sent = await feed("а" * 501)
    assert [s.text for s in sent] == [texts.QUESTION_TOO_LONG]


async def test_prashna_sends_chart_before_llm_answer(
    feed, session, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen_before_llm: list[str] = []

    async def fake_interpret(*args: Any, **kwargs: Any) -> llm.Answer:
        seen_before_llm.extend(session.texts)
        return llm.Answer(text="Ответ LLM: да.")

    monkeypatch.setattr(prashna_handlers.llm, "interpret", fake_interpret)

    sent = await feed("Получу ли я эту работу в этом году?")
    # к моменту вызова LLM карта уже ушла пользователю
    assert seen_before_llm and "Прашна принята" in seen_before_llm[0]

    texts_sent = [s.text for s in sent if s.text]
    assert "Прашна принята" in texts_sent[0]
    assert "Ответ LLM: да." in texts_sent[1]
    assert texts_sent[-1].startswith("Полная карта:")

    rows = db.history(USER_ID, 10)
    assert len(rows) == 1
    assert db.get_prashna(USER_ID, rows[0]["id"])["answer"] == "Ответ LLM: да."


async def test_prashna_keeps_chart_when_llm_fails(feed, monkeypatch: pytest.MonkeyPatch) -> None:
    async def failing(*args: Any, **kwargs: Any) -> str:
        raise llm.LLMError("Groq недоступен")

    monkeypatch.setattr(prashna_handlers.llm, "interpret", failing)
    before = db.entitlement_for(USER_ID).left
    sent = await feed("Получу ли я эту работу в этом году?")
    texts_sent = [s.text for s in sent if s.text]
    assert "Прашна принята" in texts_sent[0]
    assert texts_sent[-1] == texts.LLM_UNAVAILABLE
    assert db.history(USER_ID, 10) == []
    # Толкование не дошло — квант возвращён (§5.4.1).
    assert db.entitlement_for(USER_ID).left == before


async def test_prashna_refunds_quantum_on_unexpected_error(
    feed, monkeypatch: pytest.MonkeyPatch, no_network
) -> None:
    # Не LLMError и не сбой карты: неучтённое исключение обязано вернуть квант так же.
    async def boom(*args: Any, **kwargs: Any) -> bool:
        raise RuntimeError("неучтённый сбой")

    monkeypatch.setattr(prashna_handlers, "_answer", boom)
    before = db.entitlement_for(USER_ID).left
    with pytest.raises(RuntimeError):
        await feed("Получу ли я эту работу в этом году?")
    assert db.entitlement_for(USER_ID).left == before


async def test_prashna_refunds_quantum_when_chart_fails(
    feed, monkeypatch: pytest.MonkeyPatch, no_network
) -> None:
    def boom(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError("расчёт упал")

    monkeypatch.setattr(prashna_handlers, "build_chart", boom)
    before = db.entitlement_for(USER_ID).left
    sent = await feed("Получу ли я эту работу в этом году?")
    assert [s.text for s in sent if s.text] == [texts.CHART_FAILED]
    assert db.entitlement_for(USER_ID).left == before


async def test_chart_command_returns_saved_prashna(feed, no_network) -> None:
    pid = db.save_prashna(USER_ID, "Вопрос?", 10, "Томск", "ТЕКСТ КАРТЫ", "ТОЛКОВАНИЕ")
    sent = await feed(f"/chart {pid}")
    assert sent[-1].method == "SendDocument"
    assert f"Прашна #{pid}" in sent[-1].text


async def test_chart_command_rejects_foreign_id(feed, no_network) -> None:
    pid = db.save_prashna(USER_ID + 1, "Чужой вопрос?", 10, "Томск", "КАРТА", "ОТВЕТ")
    sent = await feed(f"/chart {pid}")
    assert [s.text for s in sent] == [texts.CHART_NOT_FOUND]


async def test_forget_clears_history(feed, no_network) -> None:
    db.save_prashna(USER_ID, "Вопрос?", 10, "Томск", "КАРТА", "ОТВЕТ")
    sent = await feed("/forget")
    assert sent[-1].text == texts.history_cleared(1)
    assert db.history(USER_ID, 10) == []


async def test_forget_does_not_promise_full_deletion(feed, no_network, user_id) -> None:
    """Имя команды обещает больше, чем она делает: ответ обязан назвать границу."""
    db.grant(user_id, "ch-1", "pack10", 100)
    sent = await feed("/forget")
    assert "/delete_me" in sent[-1].text
    assert db.entitlement_for(user_id).source == "questions"


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (llm.LLMUnavailable("прокси лёг"), texts.LLM_UNAVAILABLE),
        (llm.LLMRateLimited("429"), texts.LLM_RATE_LIMITED),
        (llm.LLMAuthError("401"), texts.LLM_UNAVAILABLE),
        (llm.LLMTimeout("не дождались"), texts.LLM_TIMEOUT),
        (llm.LLMEmptyAnswer("пусто"), texts.LLM_EMPTY),
    ],
)
async def test_llm_failures_are_explained_and_not_charged(
    error: llm.LLMError, expected: str, feed, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def failing(*args: Any, **kwargs: Any) -> llm.Answer:
        raise error

    monkeypatch.setattr(prashna_handlers.llm, "interpret", failing)
    before = db.entitlement_for(USER_ID).left
    sent = await feed("Получу ли я эту работу в этом году?")
    assert [s.text for s in sent if s.text][-1] == expected
    assert db.entitlement_for(USER_ID).left == before
    assert db.history(USER_ID, 10) == []


async def test_truncated_answer_is_delivered_marked_and_charged(
    feed, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def truncated(*args: Any, **kwargs: Any) -> llm.Answer:
        return llm.Answer(text="Вердикт: да. Обоснование обрывается на полу", truncated=True)

    monkeypatch.setattr(prashna_handlers.llm, "interpret", truncated)
    before = db.entitlement_for(USER_ID).left
    sent = await feed("Получу ли я эту работу в этом году?")
    texts_sent = [s.text for s in sent if s.text]
    assert texts.ANSWER_TRUNCATED in texts_sent
    # Толкование доставлено, пусть и сокращённое, — вопрос списан (§5.4.1).
    assert db.entitlement_for(USER_ID).left == before - 1


async def test_telegram_refusal_keeps_answer_and_refunds(
    feed, session, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def answered(*args: Any, **kwargs: Any) -> llm.Answer:
        return llm.Answer(text="Вердикт: да. " + "Обоснование. " * 20)

    monkeypatch.setattr(prashna_handlers.llm, "interpret", answered)
    before = db.entitlement_for(USER_ID).left

    real_request = session.make_request

    async def flaky(bot: Any, method: Any, timeout: Any = None) -> Any:  # noqa: ASYNC109
        # Карта проходит, само толкование Telegram не принимает.
        if "Вердикт" in (getattr(method, "text", "") or ""):
            raise TelegramBadRequest(method=method, message="message is too long")
        return await real_request(bot, method, timeout)

    monkeypatch.setattr(session, "make_request", flaky)
    await feed("Получу ли я эту работу в этом году?")
    assert any("/chart" in t and "не списан" in t for t in session.texts)
    assert db.entitlement_for(USER_ID).left == before
    # Толкование сохранено до отправки: его можно забрать командой /chart.
    assert len(db.history(USER_ID, 10)) == 1


# --- C-07: валидность в прашна-пути ---------------------------------------- #


async def test_unclear_question_is_rejected_before_reserve(
    feed, session, monkeypatch: pytest.MonkeyPatch, no_network
) -> None:
    """Вопрос без ясного дома: ни кванта, ни расчёта карты."""
    built: list[int] = []
    monkeypatch.setattr(prashna_handlers, "build_chart", lambda *a, **kw: built.append(1) or None)
    before = db.entitlement_for(USER_ID).left

    sent = await feed("ну что там вообще, интересно")

    assert built == []
    assert "не видно, о какой области жизни" in sent[-1].text
    assert db.entitlement_for(USER_ID).left == before


async def test_invalid_chart_shows_chart_and_refunds(
    feed, session, monkeypatch: pytest.MonkeyPatch, no_network
) -> None:
    """Отказ по карте: карта показана, квант возвращён, LLM не звался."""
    monkeypatch.setattr(
        prashna_handlers,
        "_validity_of",
        lambda *a, **kw: validity.Verdict(
            status=validity.REJECT, reason=validity.LAGNA_GANDANTA_REASON
        ),
    )
    before = db.entitlement_for(USER_ID).left

    sent = await feed("Получу ли я эту работу в этом году?")
    texts_sent = [s.text for s in sent if s.text]

    assert "Прашна принята" in texts_sent[0]  # расчёт показан, видно, что он был
    assert "не годится для суждения" in texts_sent[-1]
    assert "Вопрос не списан" in texts_sent[-1]
    assert db.entitlement_for(USER_ID).left == before


async def test_rejected_prashna_is_saved_with_reason(
    feed, monkeypatch: pytest.MonkeyPatch, no_network
) -> None:
    monkeypatch.setattr(
        prashna_handlers,
        "_validity_of",
        lambda *a, **kw: validity.Verdict(
            status=validity.REJECT, reason=validity.KSHINA_CHANDRA_REASON
        ),
    )
    await feed("Получу ли я эту работу в этом году?")

    with db.conn() as c:
        row = c.execute("SELECT question, reject_reason, answer FROM prashna").fetchone()
    assert row["reject_reason"] == validity.KSHINA_CHANDRA_REASON
    assert row["answer"] == ""
    # В историю отказ не идёт: толкования нет, /chart показал бы пустоту.
    assert db.history(USER_ID, 10) == []


async def test_reject_with_retry_time_tells_when(
    feed, monkeypatch: pytest.MonkeyPatch, no_network
) -> None:
    retry_at = datetime(2026, 9, 21, 12, 34, tzinfo=timezone.utc)
    monkeypatch.setattr(
        prashna_handlers,
        "_validity_of",
        lambda *a, **kw: validity.Verdict(
            status=validity.REJECT, reason=validity.LAGNA_SANDHI_REASON, retry_at=retry_at
        ),
    )
    sent = await feed("Получу ли я эту работу в этом году?")
    assert "Спросите снова после" in sent[-1].text


async def test_answered_prashna_stores_lagna(feed, monkeypatch: pytest.MonkeyPatch) -> None:
    async def answered(*args: Any, **kwargs: Any) -> llm.Answer:
        return llm.Answer(text="Вердикт: да. " + "Обоснование. " * 20)

    monkeypatch.setattr(prashna_handlers.llm, "interpret", answered)
    await feed("Получу ли я эту работу в этом году?")

    with db.conn() as c:
        row = c.execute("SELECT asc_sign, reject_reason FROM prashna").fetchone()
    # Лагна нужна правилу повторного вопроса (C-03).
    assert row["asc_sign"] is not None
    assert row["reject_reason"] is None


# --- C-08: антиабьюз отказов ----------------------------------------------- #


async def test_reject_is_counted(feed, monkeypatch: pytest.MonkeyPatch, no_network) -> None:
    monkeypatch.setattr(
        prashna_handlers,
        "_validity_of",
        lambda *a, **kw: validity.Verdict(
            status=validity.REJECT, reason=validity.LAGNA_GANDANTA_REASON
        ),
    )
    await feed("Получу ли я эту работу в этом году?")
    assert db.rejects_today(USER_ID) == 1


async def test_unclear_question_is_counted_too(feed, no_network) -> None:
    # Бессмысленный текст карту не считает, но и бесплатным циклом быть не должен.
    await feed("ну что там вообще, интересно")
    assert db.rejects_today(USER_ID) == 1


async def test_reject_cap_stops_before_any_calculation(
    feed, monkeypatch: pytest.MonkeyPatch, no_network
) -> None:
    """DoD: превышен лимит → ни расчёта карты, ни резерва."""
    built: list[int] = []
    monkeypatch.setattr(prashna_handlers, "build_chart", lambda *a, **kw: built.append(1))
    reserved: list[int] = []
    monkeypatch.setattr(db, "reserve", lambda *a, **kw: reserved.append(1) or (None, ""))

    for _ in range(C.MAX_REJECTS_PER_DAY):
        db.note_reject(USER_ID)
    before = db.entitlement_for(USER_ID).left

    sent = await feed("Получу ли я эту работу в этом году?")

    assert [s.text for s in sent if s.text] == [texts.TOO_MANY_REJECTS]
    assert built == [] and reserved == []
    assert db.entitlement_for(USER_ID).left == before


async def test_admin_is_not_capped(feed, monkeypatch: pytest.MonkeyPatch, no_network) -> None:
    monkeypatch.setattr(
        prashna_handlers, "settings", dataclasses.replace(settings, admin_ids=(USER_ID,))
    )
    for _ in range(C.MAX_REJECTS_PER_DAY + 5):
        db.note_reject(USER_ID)

    sent = await feed("ну что там вообще, интересно")
    assert texts.TOO_MANY_REJECTS not in [s.text for s in sent if s.text]


# --- C-05: оговорка на слабой карте ---------------------------------------- #


async def test_caution_is_delivered_and_charged(feed, monkeypatch: pytest.MonkeyPatch) -> None:
    """Слабая карта: толкование выдаётся с оговоркой, квант списывается."""
    monkeypatch.setattr(
        prashna_handlers,
        "_validity_of",
        lambda *a, **kw: validity.Verdict(status=validity.CAUTION, reason="Луна в 8-м доме."),
    )

    async def answered(*args: Any, **kwargs: Any) -> llm.Answer:
        return llm.Answer(text="Вердикт: да. " + "Обоснование. " * 20)

    monkeypatch.setattr(prashna_handlers.llm, "interpret", answered)
    before = db.entitlement_for(USER_ID).left

    sent = await feed("Получу ли я эту работу в этом году?")
    texts_sent = [s.text for s in sent if s.text]

    assert any("Карта слабая" in t and "Луна в 8-м доме." in t for t in texts_sent)
    assert db.entitlement_for(USER_ID).left == before - 1
    assert len(db.history(USER_ID, 10)) == 1


async def test_no_caution_note_on_a_clean_chart(feed, monkeypatch: pytest.MonkeyPatch) -> None:
    async def answered(*args: Any, **kwargs: Any) -> llm.Answer:
        return llm.Answer(text="Вердикт: да. " + "Обоснование. " * 20)

    monkeypatch.setattr(prashna_handlers.llm, "interpret", answered)
    sent = await feed("Получу ли я эту работу в этом году?")
    assert not any("Карта слабая" in (s.text or "") for s in sent)


# --- E-01: /privacy -------------------------------------------------------- #


async def test_privacy_command_answers(feed, no_network) -> None:
    sent = await feed("/privacy")
    assert sent[-1].text == texts.PRIVACY


def test_privacy_names_groq_and_proxy() -> None:
    """Текст вопроса уходит наружу — это должно быть сказано прямо, а не общими словами."""
    assert "Groq" in texts.PRIVACY
    assert "прокси" in texts.PRIVACY


def test_privacy_covers_what_is_stored() -> None:
    for item in ("вопрос", "карт", "координаты", "часовой пояс"):
        assert item in texts.PRIVACY.lower() or item in texts.PRIVACY


def test_privacy_explains_the_tombstone() -> None:
    # Надгробие из E-03: часть записи переживает удаление, и об этом нужно сказать.
    assert "/delete_me" in texts.PRIVACY
    assert "пробны" in texts.PRIVACY


def test_privacy_mentions_backups_retention() -> None:
    assert "14 дней" in texts.PRIVACY


def test_privacy_is_in_help_and_menu() -> None:
    assert "/privacy" in texts.WELCOME
    assert "privacy" in [c.command for c in bot_module.BOT_COMMANDS]


def test_privacy_fits_one_telegram_message() -> None:
    assert len(texts.PRIVACY) <= 4096
