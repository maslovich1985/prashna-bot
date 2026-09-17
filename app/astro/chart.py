"""Расчёт прашна-кундали на момент вопроса через Swiss Ephemeris (pyswisseph).

Используется сидерический зодиак (по умолчанию аянамша Лахири) и система домов
«целый знак» (whole sign), как принято в классическом джьотише.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone, timedelta
from typing import Any

import swisseph as swe

from . import constants as C

# Расчёт по встроенной аналитической теории Мошье — не требует файлов эфемерид.
FLAGS = swe.FLG_SWIEPH | swe.FLG_MOSEPH | swe.FLG_SIDEREAL | swe.FLG_SPEED

SWE_IDS = {
    "Солнце": swe.SUN,
    "Луна": swe.MOON,
    "Марс": swe.MARS,
    "Меркурий": swe.MERCURY,
    "Юпитер": swe.JUPITER,
    "Венера": swe.VENUS,
    "Сатурн": swe.SATURN,
}

AYANAMSA_MODES = {
    "LAHIRI": swe.SIDM_LAHIRI,
    "RAMAN": swe.SIDM_RAMAN,
    "KRISHNAMURTI": swe.SIDM_KRISHNAMURTI,
    "YUKTESHWAR": swe.SIDM_YUKTESHWAR,
    "TRUE_CITRA": swe.SIDM_TRUE_CITRA,
}


def norm360(x: float) -> float:
    return x % 360.0


def sign_of(lon: float) -> int:
    """Номер знака 0..11."""
    return int(norm360(lon) // 30)


def deg_in_sign(lon: float) -> float:
    return norm360(lon) % 30.0


def dms(deg: float) -> str:
    d = int(deg)
    m_full = (deg - d) * 60
    m = int(m_full)
    s = int((m_full - m) * 60)
    return f"{d}°{m:02d}'{s:02d}\""


def houses_between(from_sign: int, to_sign: int) -> int:
    """Счёт домов включительно: от знака A до знака B (1..12)."""
    return ((to_sign - from_sign) % 12) + 1


@dataclass
class PlanetPos:
    name: str
    lon: float
    speed: float
    retro: bool
    sign: int
    deg: float
    house: int
    nakshatra: int
    pada: int
    nak_lord: str
    dignity: str
    combust: bool
    shadbala_hint: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "планета": self.name,
            "знак": C.SIGNS[self.sign],
            "градус": dms(self.deg),
            "дом": self.house,
            "накшатра": f"{C.NAKSHATRAS[self.nakshatra]} пада {self.pada} (влад. {self.nak_lord})",
            "ретроград": self.retro,
            "сожжение": self.combust,
            "достоинство": self.dignity,
        }


@dataclass
class PrashnaChart:
    when_utc: datetime
    when_local: datetime
    tz_name: str
    lat: float
    lon: float
    place: str
    ayanamsa_value: float
    asc_lon: float = 0.0
    asc_sign: int = 0
    asc_deg: float = 0.0
    asc_nak: int = 0
    asc_pada: int = 0
    planets: dict[str, PlanetPos] = field(default_factory=dict)
    vargas: dict[str, dict[str, str]] = field(default_factory=dict)
    aruda_lagna: int = 0
    question_house: int = 1
    question_house_aruda: int = 0
    dasha: dict[str, str] = field(default_factory=dict)
    panchanga: dict[str, str] = field(default_factory=dict)
    aspects: dict[str, list[str]] = field(default_factory=dict)
    house_lords: dict[int, str] = field(default_factory=dict)


# --------------------------------------------------------------------------- #
# Варги
# --------------------------------------------------------------------------- #

def varga_sign(lon: float, division: int) -> int:
    s = sign_of(lon)
    d = deg_in_sign(lon)
    odd = s % 2 == 0  # Овен(0) — нечётный знак в терминах джьотиша

    if division == 1:
        return s
    if division == 2:  # Хора
        if odd:
            return 4 if d < 15 else 3
        return 3 if d < 15 else 4
    if division == 3:  # Дреккана
        return (s + [0, 4, 8][int(d // 10)]) % 12
    if division == 4:  # Чатуртхамша
        return (s + [0, 3, 6, 9][int(d // 7.5)]) % 12
    if division == 7:  # Саптамша
        start = s if odd else (s + 6) % 12
        return (start + int(d // (30 / 7))) % 12
    if division == 9:  # Навамша
        start = {0: 0, 1: 9, 2: 6, 3: 3}[s % 4]
        return (start + int(d // (30 / 9))) % 12
    if division == 10:  # Дашамша
        start = s if odd else (s + 8) % 12
        return (start + int(d // 3)) % 12
    if division == 12:  # Двадашамша
        return (s + int(d // 2.5)) % 12
    raise ValueError(f"Варга D{division} не поддерживается")


VARGAS = [1, 2, 3, 4, 7, 9, 10, 12]


# --------------------------------------------------------------------------- #
# Достоинства, сожжение, аспекты
# --------------------------------------------------------------------------- #

def dignity_of(planet: str, sign: int, deg: float) -> str:
    ex = C.EXALTATION.get(planet)
    deb = C.DEBILITATION.get(planet)
    if ex and sign == ex[0]:
        return "экзальтация" + (" (точная)" if abs(deg - ex[1]) <= 1 else "")
    if deb and sign == deb[0]:
        return "падение" + (" (точное)" if abs(deg - deb[1]) <= 1 else "")
    mt = C.MOOLATRIKONA.get(planet)
    if mt and sign == mt[0] and mt[1] <= deg <= mt[2]:
        return "мулатрикона"
    if sign in C.OWN_SIGNS.get(planet, []):
        return "свой знак"
    lord = C.SIGN_LORDS[sign]
    rel = C.NATURAL_FRIENDS.get(planet, {})
    if lord in rel.get("друзья", []):
        return "знак друга"
    if lord in rel.get("враги", []):
        return "знак врага"
    return "нейтральный знак"


def is_combust(planet: str, lon: float, sun_lon: float, retro: bool) -> bool:
    if planet in ("Солнце", "Раху", "Кету"):
        return False
    orb = C.COMBUSTION_ORB.get(planet)
    if retro and planet in C.COMBUSTION_ORB_RETRO:
        orb = C.COMBUSTION_ORB_RETRO[planet]
    if orb is None:
        return False
    diff = abs(norm360(lon - sun_lon))
    if diff > 180:
        diff = 360 - diff
    return diff <= orb


def compute_aspects(planets: dict[str, PlanetPos], asc_sign: int) -> dict[str, list[str]]:
    """Граха дришти по знакам (раши дришти опущена)."""
    result: dict[str, list[str]] = {}
    for name, p in planets.items():
        targets = [7] + C.SPECIAL_ASPECTS.get(name, [])
        aspected_signs = sorted({(p.sign + t - 1) % 12 for t in targets})
        items = []
        for sg in aspected_signs:
            house_no = houses_between(asc_sign, sg)
            occupants = [q for q, pp in planets.items() if pp.sign == sg and q != name]
            txt = f"{house_no}-й дом ({C.SIGNS[sg]})"
            if occupants:
                txt += " → " + ", ".join(occupants)
            items.append(txt)
        result[name] = items
    return result


# --------------------------------------------------------------------------- #
# Аруда
# --------------------------------------------------------------------------- #

def aruda_of_house(house_no: int, asc_sign: int, planets: dict[str, PlanetPos]) -> int:
    house_sign = (asc_sign + house_no - 1) % 12
    lord = C.SIGN_LORDS[house_sign]
    lord_sign = planets[lord].sign
    count = houses_between(house_sign, lord_sign)
    aruda = (lord_sign + count - 1) % 12
    # Правило: аруда не может совпадать с самим домом или его 7-м — берём 10-й
    if aruda == house_sign or aruda == (house_sign + 6) % 12:
        aruda = (aruda + 9) % 12
    return aruda


# --------------------------------------------------------------------------- #
# Вимшоттари даша
# --------------------------------------------------------------------------- #

def vimshottari(moon_lon: float, moment: datetime) -> dict[str, str]:
    nak_len = 360.0 / 27
    nak_index = int(norm360(moon_lon) // nak_len)
    lord = C.NAKSHATRA_LORDS[nak_index]
    passed = (norm360(moon_lon) % nak_len) / nak_len  # доля пройденной накшатры

    total = C.VIMSHOTTARI_YEARS[lord]
    balance_years = total * (1 - passed)

    year = timedelta(days=365.2425)
    start = moment - year * (total - balance_years)

    # Маха-даша
    maha_start, maha_end = start, start + year * total
    maha = lord

    # Антар-даша
    idx = C.VIMSHOTTARI_ORDER.index(maha)
    cursor = maha_start
    antar, antar_start, antar_end = maha, maha_start, maha_end
    for i in range(9):
        sub = C.VIMSHOTTARI_ORDER[(idx + i) % 9]
        dur = year * (total * C.VIMSHOTTARI_YEARS[sub] / 120)
        if cursor <= moment < cursor + dur:
            antar, antar_start, antar_end = sub, cursor, cursor + dur
            break
        cursor += dur

    # Пратьянтар-даша
    idx2 = C.VIMSHOTTARI_ORDER.index(antar)
    cursor2 = antar_start
    antar_len = antar_end - antar_start
    pratyantar = antar
    for i in range(9):
        sub = C.VIMSHOTTARI_ORDER[(idx2 + i) % 9]
        dur = antar_len * (C.VIMSHOTTARI_YEARS[sub] / 120)
        if cursor2 <= moment < cursor2 + dur:
            pratyantar = sub
            break
        cursor2 += dur

    return {
        "маха": maha,
        "антар": antar,
        "пратьянтар": pratyantar,
        "маха_до": maha_end.strftime("%d.%m.%Y"),
        "антар_до": antar_end.strftime("%d.%m.%Y"),
        "строка": f"{maha} / {antar} / {pratyantar}",
    }


# --------------------------------------------------------------------------- #
# Панчанга
# --------------------------------------------------------------------------- #

def panchanga(sun_lon: float, moon_lon: float, local: datetime) -> dict[str, str]:
    diff = norm360(moon_lon - sun_lon)
    tithi_idx = int(diff // 12)
    paksha = "Шукла (растущая)" if tithi_idx < 15 else "Кришна (убывающая)"
    tithi_name = C.TITHI_NAMES[tithi_idx % 15]

    yoga_idx = int(norm360(sun_lon + moon_lon) // (360 / 27))
    karana_idx = int(diff // 6)
    if karana_idx == 0:
        karana = "Кимстугхна"
    elif karana_idx >= 57:
        karana = ["Шакуни", "Чатушпада", "Нага"][karana_idx - 57]
    else:
        karana = C.KARANA_NAMES[(karana_idx - 1) % 7]

    weekday = local.weekday()  # 0 = понедельник
    day_lord = C.WEEKDAY_LORDS[weekday]
    # Хора: приближённо от 06:00 местного времени, по 1 часу
    hours_since = (local.hour - 6) % 24
    start = C.CHALDEAN.index(day_lord)
    hora_lord = C.CHALDEAN[(start + hours_since) % 7]

    return {
        "титхи": f"{tithi_name}, {paksha}",
        "накшатра_луны": C.NAKSHATRAS[int(norm360(moon_lon) // (360 / 27))],
        "йога": C.YOGA_NAMES[yoga_idx],
        "карана": karana,
        "вара": f"{['Понедельник','Вторник','Среда','Четверг','Пятница','Суббота','Воскресенье'][weekday]} (владыка дня {day_lord})",
        "хора": f"владыка хоры {hora_lord}",
    }


# --------------------------------------------------------------------------- #
# Главная функция
# --------------------------------------------------------------------------- #

def build_chart(
    moment_utc: datetime,
    lat: float,
    lon: float,
    tz_name: str,
    place: str,
    question_house: int = 1,
    ayanamsa: str = "LAHIRI",
) -> PrashnaChart:
    swe.set_sid_mode(AYANAMSA_MODES.get(ayanamsa.upper(), swe.SIDM_LAHIRI), 0, 0)

    ut = moment_utc.astimezone(timezone.utc)
    jd = swe.julday(
        ut.year, ut.month, ut.day,
        ut.hour + ut.minute / 60 + ut.second / 3600,
        swe.GREG_CAL,
    )
    ayan = swe.get_ayanamsa_ut(jd)

    try:
        local = moment_utc.astimezone(_tz(tz_name))
    except Exception:
        local = moment_utc

    chart = PrashnaChart(
        when_utc=ut, when_local=local, tz_name=tz_name, lat=lat, lon=lon,
        place=place, ayanamsa_value=ayan,
    )

    # Асцендент (Лагна), дома — целый знак
    cusps, ascmc = swe.houses_ex(jd, lat, lon, b"W", swe.FLG_SIDEREAL)
    chart.asc_lon = norm360(ascmc[0])
    chart.asc_sign = sign_of(chart.asc_lon)
    chart.asc_deg = deg_in_sign(chart.asc_lon)
    nak_len = 360 / 27
    chart.asc_nak = int(chart.asc_lon // nak_len)
    chart.asc_pada = int((chart.asc_lon % nak_len) // (nak_len / 4)) + 1

    # Планеты
    raw: dict[str, tuple[float, float]] = {}
    for name, pid in SWE_IDS.items():
        vals, _ = swe.calc_ut(jd, pid, FLAGS)
        raw[name] = (norm360(vals[0]), vals[3])

    rahu_vals, _ = swe.calc_ut(jd, swe.MEAN_NODE, FLAGS)
    rahu_lon = norm360(rahu_vals[0])
    raw["Раху"] = (rahu_lon, rahu_vals[3])
    raw["Кету"] = (norm360(rahu_lon + 180), rahu_vals[3])

    sun_lon = raw["Солнце"][0]

    for name in C.PLANETS:
        plon, speed = raw[name]
        retro = speed < 0 or name in ("Раху", "Кету")
        s = sign_of(plon)
        d = deg_in_sign(plon)
        nk = int(plon // nak_len)
        chart.planets[name] = PlanetPos(
            name=name, lon=plon, speed=speed, retro=retro, sign=s, deg=d,
            house=houses_between(chart.asc_sign, s),
            nakshatra=nk,
            pada=int((plon % nak_len) // (nak_len / 4)) + 1,
            nak_lord=C.NAKSHATRA_LORDS[nk],
            dignity=dignity_of(name, s, d),
            combust=is_combust(name, plon, sun_lon, retro),
        )

    # Варги
    for dv in VARGAS:
        row = {"Лагна": C.SIGNS[varga_sign(chart.asc_lon, dv)]}
        for name in C.PLANETS:
            row[name] = C.SIGNS[varga_sign(chart.planets[name].lon, dv)]
        chart.vargas[f"D{dv}"] = row

    # Владыки домов и их положение
    for h in range(1, 13):
        hs = (chart.asc_sign + h - 1) % 12
        lord = C.SIGN_LORDS[hs]
        lp = chart.planets[lord]
        chart.house_lords[h] = (
            f"{lord} — в {lp.house}-м доме ({C.SIGNS[lp.sign]}), {lp.dignity}"
            + (", ретро" if lp.retro else "")
            + (", сожжён" if lp.combust else "")
        )

    chart.aruda_lagna = aruda_of_house(1, chart.asc_sign, chart.planets)
    chart.question_house = question_house
    chart.question_house_aruda = aruda_of_house(question_house, chart.asc_sign, chart.planets)
    chart.aspects = compute_aspects(chart.planets, chart.asc_sign)
    chart.dasha = vimshottari(chart.planets["Луна"].lon, moment_utc)
    chart.panchanga = panchanga(sun_lon, chart.planets["Луна"].lon, local)

    return chart


def _tz(name: str):
    from zoneinfo import ZoneInfo
    return ZoneInfo(name)
