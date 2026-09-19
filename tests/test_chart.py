"""Эталонная карта: фиксированный момент и координаты → фиксированные позиции.

Эталон снят с рабочей версии расчёта (Swiss Ephemeris, FLG_MOSEPH, аянамша Лахири).
Расхождение означает, что изменилась логика расчёта, а не «плавающие» данные:
моссефские позиции детерминированы и файлов эфемерид не требуют.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.astro import constants as C
from app.astro.chart import PrashnaChart, build_chart

MOMENT = datetime(2026, 3, 15, 9, 30, 0, tzinfo=timezone.utc)
LAT, LON, TZ = 55.7558, 37.6173, "Europe/Moscow"

# планета → (знак, дом, долгота, ретроградность)
EXPECTED_PLANETS = {
    "Солнце": (11, 9, 330.579406, False),
    "Луна": (9, 7, 285.359241, False),
    "Марс": (10, 8, 315.858484, False),
    "Меркурий": (10, 8, 315.781389, True),
    "Юпитер": (2, 12, 80.891771, False),
    "Венера": (11, 9, 346.891575, False),
    "Сатурн": (11, 9, 339.253448, False),
    "Раху": (10, 8, 314.059516, True),
    "Кету": (4, 2, 134.059516, True),
}


@pytest.fixture(scope="module")
def chart() -> PrashnaChart:
    return build_chart(MOMENT, LAT, LON, TZ, "Москва", question_house=7)


def test_ascendant(chart: PrashnaChart) -> None:
    assert chart.asc_sign == 3
    assert chart.asc_deg == pytest.approx(1.317612, abs=1e-4)
    assert (chart.asc_nak, chart.asc_pada) == (6, 4)


def test_ayanamsa(chart: PrashnaChart) -> None:
    assert chart.ayanamsa_value == pytest.approx(24.223111, abs=1e-4)


def test_nine_planets(chart: PrashnaChart) -> None:
    assert list(chart.planets) == list(C.PLANETS)
    assert len(chart.planets) == 9


@pytest.mark.parametrize("name", EXPECTED_PLANETS)
def test_planet_positions(chart: PrashnaChart, name: str) -> None:
    sign, house, lon, retro = EXPECTED_PLANETS[name]
    p = chart.planets[name]
    assert (p.sign, p.house, p.retro) == (sign, house, retro)
    assert p.lon == pytest.approx(lon, abs=1e-4)


def test_eight_vargas(chart: PrashnaChart) -> None:
    assert sorted(chart.vargas) == ["D1", "D10", "D12", "D2", "D3", "D4", "D7", "D9"]
    for table in chart.vargas.values():
        assert set(table) == set(C.PLANETS) | {"Лагна"}


def test_question_house_kept(chart: PrashnaChart) -> None:
    assert chart.question_house == 7
    assert chart.house_lords[7].startswith(C.SIGN_LORDS[(chart.asc_sign + 6) % 12])


def test_dasha_and_panchanga_filled(chart: PrashnaChart) -> None:
    assert chart.dasha["строка"]
    assert chart.panchanga["титхи"] and chart.panchanga["накшатра_луны"]


def test_local_time_converted(chart: PrashnaChart) -> None:
    pytest.importorskip("zoneinfo")  # без tzdata build_chart молча оставляет UTC
    assert chart.when_local.hour == 12  # UTC+3, перехода на летнее время в Москве нет
    assert chart.when_utc == MOMENT
