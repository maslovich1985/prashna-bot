"""Рендеры карты и список факторов."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.astro.chart import PrashnaChart, build_chart
from app.astro.prashna import judgment_factors, render_chart_text, render_short

TELEGRAM_LIMIT = 4096


@pytest.fixture(scope="module")
def chart() -> PrashnaChart:
    return build_chart(
        datetime(2026, 3, 15, 9, 30, 0, tzinfo=timezone.utc),
        55.7558,
        37.6173,
        "Europe/Moscow",
        "Москва",
        question_house=7,
    )


def test_chart_text_fits_one_message(chart: PrashnaChart) -> None:
    text = render_chart_text(chart)
    assert "Лагна:" in text
    assert len(text) <= TELEGRAM_LIMIT


def test_short_fits_one_message(chart: PrashnaChart) -> None:
    short = render_short(chart)
    assert "Лагна:" in short
    assert len(short) <= TELEGRAM_LIMIT


def test_short_mentions_question_house(chart: PrashnaChart) -> None:
    assert "Дом вопроса:</b> 7" in render_short(chart)


def test_chart_text_lists_all_planets(chart: PrashnaChart) -> None:
    text = render_chart_text(chart)
    for name in chart.planets:
        assert name in text


def test_judgment_factors(chart: PrashnaChart) -> None:
    factors = judgment_factors(chart)
    assert len(factors) > 8
    assert all(isinstance(f, str) and f.strip() for f in factors)
    assert factors[0].startswith("Лагна:")


@pytest.mark.parametrize("house", range(1, 13))
def test_factors_for_every_question_house(house: int) -> None:
    c = build_chart(
        datetime(2026, 3, 15, 9, 30, 0, tzinfo=timezone.utc),
        55.7558,
        37.6173,
        "Europe/Moscow",
        "Москва",
        question_house=house,
    )
    assert len(judgment_factors(c)) > 8
    assert len(render_chart_text(c)) <= TELEGRAM_LIMIT
