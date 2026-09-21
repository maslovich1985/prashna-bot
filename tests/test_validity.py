"""Каркас проверки валидности прашны (C-01): вердикт, чистота модуля, пустые правила."""

from __future__ import annotations

import ast
import dataclasses
from dataclasses import FrozenInstanceError
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import swisseph as swe

from app.astro import chart as chart_module
from app.astro import constants as C
from app.astro import validity
from app.astro.chart import Sky, build_chart

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
    assert validity.REJECT_RULES == [
        validity.no_clear_house,
        validity.lagna_gandanta,
        validity.lagna_bhava_sandhi,
        validity.moon_gandanta,
        validity.kshina_chandra,
    ]
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


# --- C-03: повторный вопрос ------------------------------------------------ #


SAME_LAGNA = object()  # отличает «ту же лагну» от честного None у старых прашн


def _past(chart, question: str, pid: int = 7, asc_sign=SAME_LAGNA):
    return validity.PastAsk(
        pid=pid, question=question, asc_sign=chart.asc_sign if asc_sign is SAME_LAGNA else asc_sign
    )


def test_same_question_same_lagna_is_rejected(chart) -> None:
    history = [_past(chart, "Получу ли я эту работу?")]
    verdict = validity.check(chart, "получу ли я эту работу", user_id=1, history=history)
    assert verdict.rejected
    assert "/chart 7" in verdict.reason
    # Срока нет: ответ уже дан, ждать нечего — есть ссылка на прежнее толкование.
    assert verdict.retry_at is None


def test_reformulated_question_is_still_a_repeat(chart) -> None:
    history = [_past(chart, "Получу ли я эту работу?")]
    verdict = validity.check(chart, "Получу ли я эту работу!!!", user_id=1, history=history)
    assert verdict.rejected


def test_different_question_passes(chart) -> None:
    history = [_past(chart, "Получу ли я эту работу?")]
    verdict = validity.check(
        chart, "Вернёт ли он долг до конца месяца?", user_id=1, history=history
    )
    assert verdict.status == validity.OK


def test_same_question_other_lagna_passes(chart) -> None:
    # Лагна сменилась — карта отвечает уже не то же самое.
    history = [_past(chart, "Получу ли я эту работу?", asc_sign=(chart.asc_sign + 1) % 12)]
    verdict = validity.check(chart, "Получу ли я эту работу?", user_id=1, history=history)
    assert verdict.status == validity.OK


def test_old_prashna_without_lagna_is_skipped(chart) -> None:
    # Прашны до C-03 хранятся без asc_sign: лучше не проверить, чем угадать.
    history = [_past(chart, "Получу ли я эту работу?", asc_sign=None)]
    assert validity.check(chart, "Получу ли я эту работу?", user_id=1, history=history).status == (
        validity.OK
    )


def test_empty_history_means_no_check(chart) -> None:
    assert validity.check(chart, "Получу ли я эту работу?", user_id=1).status == validity.OK


@pytest.mark.parametrize(
    ("a", "b"),
    [
        ("Получу ли я эту работу?", "получу ли я эту работу"),
        ("Вернёт ли он долг?", "Вернет ли он долг"),
        ("Стоит ли переезжать?", "  стоит   ли   переезжать!!!  "),
    ],
)
def test_normalization_ignores_case_yo_and_punctuation(a: str, b: str) -> None:
    assert validity.normalize(a) == validity.normalize(b)
    assert validity.similarity(a, b) == 1.0


def test_similarity_threshold_comes_from_constants(monkeypatch: pytest.MonkeyPatch, chart) -> None:
    history = [_past(chart, "Получу ли я эту работу?")]
    monkeypatch.setattr(C, "REPEAT_SIMILARITY", 0.01)
    assert validity.check(
        chart, "Совсем другой вопрос о долге", user_id=1, history=history
    ).rejected


def test_repeat_wins_over_other_rules(chart) -> None:
    # Повтор проверяется первым: это дешевле любого астрологического правила.
    history = [_past(chart, "ну что там вообще")]
    verdict = validity.check(chart, "ну что там вообще", user_id=1, history=history)
    assert "/chart 7" in verdict.reason


# --- C-04: геометрические правила ------------------------------------------ #


def _at(chart, *, asc_lon=None, moon_lon=None, sun_lon=None):
    """Карта с подменёнными долготами: правило проверяется на геометрии, а не на дате."""
    planets = dict(chart.planets)
    if moon_lon is not None:
        planets["Луна"] = dataclasses.replace(planets["Луна"], lon=moon_lon)
    if sun_lon is not None:
        planets["Солнце"] = dataclasses.replace(planets["Солнце"], lon=sun_lon)
    return dataclasses.replace(
        chart, asc_lon=chart.asc_lon if asc_lon is None else asc_lon, planets=planets
    )


@pytest.mark.parametrize("lon", [0.0, 119.5, 120.0, 121.9, 239.0, 359.5])
def test_lagna_in_gandanta_is_rejected(chart, lon: float) -> None:
    verdict = validity.check(_at(chart, asc_lon=lon), "Получу ли я эту работу?", user_id=1)
    assert verdict.rejected
    assert "гандānта" in verdict.reason


@pytest.mark.parametrize("lon", [15.0, 105.0, 135.0, 225.0, 315.0])
def test_lagna_away_from_junctions_passes(chart, lon: float) -> None:
    assert validity.check(_at(chart, asc_lon=lon), "Получу ли я эту работу?", user_id=1).status == (
        validity.OK
    )


@pytest.mark.parametrize("lon", [29.5, 30.0, 31.5, 59.0, 271.0])
def test_lagna_near_sign_border_is_rejected(chart, lon: float) -> None:
    verdict = validity.check(_at(chart, asc_lon=lon), "Получу ли я эту работу?", user_id=1)
    assert verdict.rejected
    assert "границы знака" in verdict.reason


def test_gandanta_reason_wins_over_sandhi(chart) -> None:
    # 0° — и стык знаков, и гандānта. Причина должна быть конкретнее.
    verdict = validity.check(_at(chart, asc_lon=0.0), "Получу ли я эту работу?", user_id=1)
    assert "гандānта" in verdict.reason


@pytest.mark.parametrize("lon", [0.5, 120.0, 241.0])
def test_moon_in_gandanta_nakshatra_is_rejected(chart, lon: float) -> None:
    verdict = validity.check(
        _at(chart, asc_lon=75.0, moon_lon=lon, sun_lon=200.0), "Получу ли я эту работу?", user_id=1
    )
    assert verdict.rejected
    assert "Луна на стыке" in verdict.reason


@pytest.mark.parametrize(("moon", "sun"), [(100.0, 100.0), (95.0, 100.0), (350.0, 355.0)])
def test_kshina_chandra_is_rejected(chart, moon: float, sun: float) -> None:
    verdict = validity.check(
        _at(chart, asc_lon=75.0, moon_lon=moon, sun_lon=sun), "Получу ли я эту работу?", user_id=1
    )
    assert verdict.rejected
    assert "новолуние" in verdict.reason.lower() or "кшина" in verdict.reason.lower()


def test_moon_far_from_sun_passes(chart) -> None:
    verdict = validity.check(
        _at(chart, asc_lon=75.0, moon_lon=100.0, sun_lon=200.0),
        "Получу ли я эту работу?",
        user_id=1,
    )
    assert verdict.status == validity.OK


def test_arc_distance_wraps_around_zero() -> None:
    # 359° и 1° — это 2°, а не 358°: иначе правила молчат ровно там, где должны срабатывать.
    assert validity.arc_distance(359.0, 1.0) == pytest.approx(2.0)
    assert validity.arc_distance(1.0, 359.0) == pytest.approx(2.0)
    assert validity.gandanta_distance(359.0) == pytest.approx(1.0)


@pytest.mark.parametrize(("orb_name", "lon"), [("GANDANTA_ORB", 118.5), ("BHAVA_SANDHI_ORB", 28.5)])
def test_orbs_come_from_constants(monkeypatch: pytest.MonkeyPatch, chart, orb_name, lon) -> None:
    question = "Получу ли я эту работу?"
    monkeypatch.setattr(C, "GANDANTA_ORB", 0.1)
    monkeypatch.setattr(C, "BHAVA_SANDHI_ORB", 0.1)
    assert validity.check(_at(chart, asc_lon=lon), question, user_id=1).status == validity.OK
    monkeypatch.setattr(C, orb_name, 3.0)
    assert validity.check(_at(chart, asc_lon=lon), question, user_id=1).rejected


# --- C-04 на настоящей эфемериде ------------------------------------------- #

# Под CPython 3.14 колёс pyswisseph нет, и локально в PYTHONPATH может лежать заглушка.
# Эти тесты имеют смысл только с настоящей библиотекой — в CI она настоящая.
real_ephemeris = pytest.mark.skipif(
    not hasattr(swe, "version"), reason="pyswisseph подменён заглушкой"
)

TOMSK = (56.5, 84.97, "Asia/Tomsk", "Томск")


def _chart_at(moment: datetime):
    return build_chart(moment, *TOMSK, 10)


@real_ephemeris
def test_lagna_gandanta_on_a_real_moment() -> None:
    """Ищем момент, когда лагна входит в гандānту, и проверяем правило на нём."""
    start = datetime(2026, 9, 21, 0, 0, tzinfo=timezone.utc)
    hit = next(
        (
            start + timedelta(minutes=step)
            for step in range(0, 24 * 60, 2)
            if validity.gandanta_distance(_chart_at(start + timedelta(minutes=step)).asc_lon)
            <= C.GANDANTA_ORB
        ),
        None,
    )
    assert hit is not None, "за сутки лагна обязана пройти хотя бы один стык"
    verdict = validity.check(_chart_at(hit), "Получу ли я эту работу?", user_id=1)
    assert verdict.rejected
    assert "гандānта" in verdict.reason


@real_ephemeris
def test_rejects_stay_a_minority_over_a_day() -> None:
    """Главная защита от ложных отказов: бот, отказывающий в половине случаев, сломан.

    Орб 2° из 30 даёт ~13% на бхава-сандхи плюс редкие лунные правила. Если доля
    уходит заметно выше, условие где-то инвертировано.
    """
    start = datetime(2026, 9, 21, 0, 0, tzinfo=timezone.utc)
    moments = [start + timedelta(minutes=step) for step in range(0, 24 * 60, 10)]
    verdicts = [validity.check(_chart_at(m), "Получу ли я эту работу?", user_id=1) for m in moments]
    share = sum(v.rejected for v in verdicts) / len(verdicts)
    assert 0 < share < 0.30, f"доля отказов за сутки: {share:.0%}"


# --- C-06: «когда попробовать снова» --------------------------------------- #


def _fake_sky(schedule: dict[int, tuple[float, float, float]], default):
    """Срез неба по минутам от начала: тест задаёт, когда именно станет чисто."""
    start = MOMENT

    def sky_at(moment: datetime) -> Sky:
        minute = round((moment - start).total_seconds() / 60)
        asc, moon, sun = schedule.get(minute, default)
        return Sky(asc_lon=asc, moon_lon=moon, sun_lon=sun)

    return sky_at


BLOCKED = (0.0, 60.0, 200.0)  # лагна в гандānте
CLEAR = (75.0, 60.0, 200.0)


def test_retry_moment_is_the_first_clear_minute() -> None:
    sky_at = _fake_sky({7: CLEAR, 8: CLEAR}, BLOCKED)
    found = validity.find_retry_moment(MOMENT, sky_at)
    assert found == MOMENT + timedelta(minutes=7)


def test_retry_moment_is_in_the_future() -> None:
    sky_at = _fake_sky({0: CLEAR, 5: CLEAR}, BLOCKED)
    found = validity.find_retry_moment(MOMENT, sky_at)
    # Нулевая минута — это сам момент вопроса, он уже отказной.
    assert found > MOMENT


def test_no_retry_moment_when_nothing_clears() -> None:
    # Обещать срок, которого нет, хуже, чем не обещать: None честнее.
    assert validity.find_retry_moment(MOMENT, _fake_sky({}, BLOCKED)) is None


def test_retry_search_respects_the_limit() -> None:
    sky_at = _fake_sky({C.RETRY_SEARCH_MINUTES + 10: CLEAR}, BLOCKED)
    assert validity.find_retry_moment(MOMENT, sky_at) is None


def test_geometric_reject_carries_retry_at(chart) -> None:
    sky_at = _fake_sky({12: CLEAR}, BLOCKED)
    verdict = validity.check(
        _at(chart, asc_lon=0.0), "Получу ли я эту работу?", user_id=1, sky_at=sky_at
    )
    assert verdict.rejected
    assert verdict.retry_at == chart.when_utc + timedelta(minutes=12)


def test_retry_moment_really_passes_the_check(chart) -> None:
    """DoD: в найденный момент проверка действительно даёт ok, а не только геометрия."""
    sky_at = _fake_sky({9: CLEAR}, BLOCKED)
    verdict = validity.check(
        _at(chart, asc_lon=0.0), "Получу ли я эту работу?", user_id=1, sky_at=sky_at
    )
    sky = sky_at(verdict.retry_at)
    future = _at(chart, asc_lon=sky.asc_lon, moon_lon=sky.moon_lon, sun_lon=sky.sun_lon)
    assert validity.check(future, "Получу ли я эту работу?", user_id=1).status == validity.OK


@pytest.mark.parametrize(
    ("question", "history_factory"),
    [
        ("ну что там вообще", lambda chart: []),
        ("Получу ли я эту работу?", lambda chart: [_past(chart, "Получу ли я эту работу?")]),
    ],
)
def test_non_geometric_rejects_have_no_retry(chart, question, history_factory) -> None:
    # Переформулировка и повтор от ожидания не лечатся — срок был бы ложью.
    verdict = validity.check(
        chart,
        question,
        user_id=1,
        history=history_factory(chart),
        sky_at=_fake_sky({1: CLEAR}, BLOCKED),
    )
    assert verdict.rejected
    assert verdict.retry_at is None


def test_check_without_sky_at_leaves_retry_empty(chart) -> None:
    verdict = validity.check(_at(chart, asc_lon=0.0), "Получу ли я эту работу?", user_id=1)
    assert verdict.rejected and verdict.retry_at is None


@real_ephemeris
def test_sky_at_matches_the_full_chart() -> None:
    """Дешёвый срез обязан совпадать с полной картой, иначе перебор ищет не то."""
    moment = datetime(2026, 9, 21, 10, 0, tzinfo=timezone.utc)
    sky = chart_module.sky_at(moment, 56.5, 84.97)
    full = _chart_at(moment)
    assert sky.asc_lon == pytest.approx(full.asc_lon, abs=1e-6)
    assert sky.moon_lon == pytest.approx(full.planets["Луна"].lon, abs=1e-6)
    assert sky.sun_lon == pytest.approx(full.planets["Солнце"].lon, abs=1e-6)


@real_ephemeris
def test_retry_moment_on_a_real_sky() -> None:
    """Поиск на настоящей эфемериде: найденный момент проходит проверку целиком."""
    start = datetime(2026, 9, 21, 0, 0, tzinfo=timezone.utc)
    blocked = next(
        (
            start + timedelta(minutes=step)
            for step in range(0, 24 * 60, 2)
            if validity.geometry_reason(
                chart_module.sky_at(start + timedelta(minutes=step), 56.5, 84.97)
            )
        ),
        None,
    )
    assert blocked is not None

    found = validity.find_retry_moment(blocked, lambda m: chart_module.sky_at(m, 56.5, 84.97))
    assert found is not None and found > blocked
    assert validity.check(_chart_at(found), "Получу ли я эту работу?", user_id=1).status == (
        validity.OK
    )
