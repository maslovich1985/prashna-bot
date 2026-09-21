"""Каркас проверки валидности прашны (C-01): вердикт, чистота модуля, пустые правила."""

from __future__ import annotations

import ast
from dataclasses import FrozenInstanceError
from datetime import datetime, timezone
from pathlib import Path

import pytest

from app.astro import constants as C
from app.astro import validity
from app.astro.chart import build_chart

MOMENT = datetime(2026, 9, 21, 10, 0, tzinfo=timezone.utc)


@pytest.fixture
def chart():
    return build_chart(MOMENT, 56.5, 84.97, "Asia/Tomsk", "Томск", 10)


def test_normal_chart_is_ok(chart) -> None:
    verdict = validity.check(chart, "Получу ли я эту работу?", user_id=1)
    assert verdict.status == validity.OK
    assert (verdict.rejected, verdict.cautioned) == (False, False)
    assert verdict.reason == ""
    assert verdict.retry_at is None


def test_verdict_is_frozen() -> None:
    verdict = validity.Verdict()
    with pytest.raises(FrozenInstanceError):
        verdict.status = validity.REJECT


def test_reject_rule_short_circuits(monkeypatch: pytest.MonkeyPatch, chart) -> None:
    seen: list[str] = []

    def first(_chart, _question):
        seen.append("first")
        return validity.Verdict(status=validity.REJECT, reason="стык знаков")

    def second(_chart, _question):
        seen.append("second")
        return validity.Verdict()

    monkeypatch.setattr(validity, "REJECT_RULES", [first, second])
    verdict = validity.check(chart, "Получу ли я эту работу?", user_id=1)
    assert verdict.rejected and verdict.reason == "стык знаков"
    assert seen == ["first"]  # второе правило не считается зря


def test_cautions_are_collected(monkeypatch: pytest.MonkeyPatch, chart) -> None:
    def weak(reason: str):
        return lambda _chart, _question: validity.Verdict(status=validity.CAUTION, reason=reason)

    monkeypatch.setattr(validity, "CAUTION_RULES", [weak("Луна в 8-м."), weak("Хозяин сожжён.")])
    verdict = validity.check(chart, "Получу ли я эту работу?", user_id=1)
    assert verdict.cautioned
    assert verdict.reason == "Луна в 8-м. Хозяин сожжён."


def test_only_enabled_rules_are_listed() -> None:
    # Правила включаются по одному в C-02…C-05, каждое со своим тестом.
    assert validity.REJECT_RULES == [validity.no_clear_house]
    assert validity.CAUTION_RULES == []


def test_module_stays_pure() -> None:
    """Ни db, ни llm, ни aiogram: validity считает по карте и тексту, больше ни по чему."""
    tree = ast.parse(Path(validity.__file__).read_text(encoding="utf-8"))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported.add(node.module or "")
    assert not {i for i in imported if "db" in i or "llm" in i or "aiogram" in i}


def test_thresholds_live_in_constants() -> None:
    # Тюнинг правил — правка таблицы, а не логики (инвариант CLAUDE.md).
    assert C.HOUSE_MIN_SCORE > 0
    assert C.BHAVA_SANDHI_ORB > 0
    assert C.GANDANTA_ORB > 0
    assert C.GANDANTA_SIGNS == (3, 7, 11)
    assert C.REPEAT_WINDOW_HOURS > 0
    assert C.MAX_REJECTS_PER_DAY > 0
    assert C.RETRY_SEARCH_MINUTES > C.RETRY_SEARCH_STEP_MINUTES > 0


# --- C-02: вопрос без ясного дома ------------------------------------------ #


@pytest.mark.parametrize(
    "question", ["ну что там вообще", "ааа", "", "?", "просто интересно как дела"]
)
def test_question_without_house_is_rejected(chart, question: str) -> None:
    verdict = validity.check(chart, question, user_id=1)
    assert verdict.rejected
    assert "о какой области жизни" in verdict.reason
    # Ждать нечего: помогает переформулировка, а не время.
    assert verdict.retry_at is None


@pytest.mark.parametrize(
    "question",
    [
        "Получу ли я оффер на новую работу?",
        "Вернёт ли он долг до конца месяца?",
        "Стоит ли переезжать в эту квартиру?",
        "Выздоровеет ли мама после операции?",
    ],
)
def test_clear_question_passes(chart, question: str) -> None:
    assert validity.check(chart, question, user_id=1).status == validity.OK


def test_rule_uses_the_threshold_from_constants(monkeypatch: pytest.MonkeyPatch, chart) -> None:
    # Порог крутится правкой таблицы: подняли выше любого балла — отказ на всём.
    monkeypatch.setattr(C, "HOUSE_MIN_SCORE", 10_000)
    assert validity.check(chart, "Получу ли я оффер на новую работу?", user_id=1).rejected
