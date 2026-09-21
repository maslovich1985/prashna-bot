"""Пригодна ли прашна к суждению (§5.5).

Классические признаки непригодной карты считает код, а не LLM: модуль остаётся
чистым — ни `db`, ни `llm`, ни aiogram, только карта и текст вопроса. Правила
добавляются по одному (C-02…C-05), здесь — каркас и общий вердикт.
"""

from __future__ import annotations

import difflib
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime

from . import constants as C
from .chart import PrashnaChart
from .prashna import detect_house_scored

NO_CLEAR_HOUSE_REASON = (
    "По формулировке не видно, о какой области жизни вопрос, — "
    "прашна строится на доме вопроса, и без него толкование будет ни о чём. "
    "Спросите об одном конкретном деле: «получу ли я эту работу», "
    "«вернёт ли он долг», «стоит ли переезжать в эту квартиру»."
)


LAGNA_GANDANTA_REASON = (
    "Лагна стоит на стыке водного и огненного знаков (гандānта) — "
    "классически такая карта не читается: она описывает узел, а не развитие события."
)

LAGNA_SANDHI_REASON = (
    "Лагна в считаных минутах от границы знака (бхава-сандхи). "
    "Дома в этой карте привязаны к целым знакам, поэтому такая мелочь "
    "переносит вопрос в соседний дом, и суждение получится о другом."
)

MOON_GANDANTA_REASON = (
    "Луна на стыке накшатр гандānты — ум вопрошающего сейчас между двумя состояниями, "
    "и карта отражает эту неопределённость, а не ответ."
)

KSHINA_CHANDRA_REASON = (
    "Луна в новолунии (кшина-чандра): она лишена силы, а в прашне именно Луна "
    "несёт вопрос. Толкование по такой карте классически считается недостоверным."
)


def repeat_reason(pid: int) -> str:
    return (
        "Этот вопрос вы уже задавали, и карта с тех пор почти не изменилась. "
        "Прашна отвечает на вопрос один раз: перебор формулировок ради другого ответа "
        f"ломает саму логику метода. Прежнее толкование: /chart {pid}"
    )


OK = "ok"
CAUTION = "caution"
REJECT = "reject"


@dataclass(frozen=True)
class Verdict:
    """Итог проверки.

    `reject` — карта непригодна: квант возвращается, LLM не вызывается.
    `caution` — читаема, но слабая: толкование выдаётся с оговоркой.
    `retry_at` заполняется только там, где ожидание помогает (лагна уйдёт со стыка);
    для повторного вопроса и невнятной формулировки срок бессмыслен.
    """

    status: str = OK
    reason: str = ""
    retry_at: datetime | None = None

    @property
    def rejected(self) -> bool:
        return self.status == REJECT

    @property
    def cautioned(self) -> bool:
        return self.status == CAUTION


def no_clear_house(_chart: PrashnaChart, question: str) -> Verdict:
    """Вопрос без ясного дома: ни одно ключевое слово не набрало `HOUSE_MIN_SCORE`.

    Самое частое и дешёвое правило. `detect_house` в таком случае молча отдаёт дом 1,
    и толкование выходит уверенным, но ни о чём. Ждать тут нечего — помогает только
    переформулировка, поэтому `retry_at` остаётся пустым.
    """
    _house, score = detect_house_scored(question)
    if score >= C.HOUSE_MIN_SCORE:
        return Verdict()
    return Verdict(status=REJECT, reason=NO_CLEAR_HOUSE_REASON)


@dataclass(frozen=True)
class PastAsk:
    """Заданный ранее вопрос. Ровно то, что нужно правилу, — без строки БД целиком."""

    pid: int
    question: str
    asc_sign: int | None


def normalize(text: str) -> str:
    """Приводит формулировку к виду, в котором её можно сравнивать.

    Регистр, «ё», знаки препинания и лишние пробелы не меняют сути вопроса,
    а `difflib` считает их отличиями.
    """
    text = text.lower().replace("ё", "е")
    text = re.sub(r"[^а-яa-z0-9 ]+", " ", text)
    return " ".join(text.split())


def similarity(a: str, b: str) -> float:
    """Близость формулировок, 0..1. Без внешних библиотек — хватает `difflib`."""
    return difflib.SequenceMatcher(None, normalize(a), normalize(b)).ratio()


def repeated_question(chart: PrashnaChart, question: str, history: Sequence[PastAsk]) -> Verdict:
    """Тот же вопрос при той же лагне — отказ со ссылкой на прежнее толкование.

    Лагна проходит знак ~2 часа: пока она не сменилась, карта отвечает то же самое,
    и новый расчёт даст лишь иллюзию второго мнения. Прашны без `asc_sign` (заданные
    до C-03) пропускаются: угадывать лагну задним числом хуже, чем не проверять.
    """
    for past in history:
        if past.asc_sign is None or past.asc_sign != chart.asc_sign:
            continue
        if similarity(question, past.question) >= C.REPEAT_SIMILARITY:
            return Verdict(status=REJECT, reason=repeat_reason(past.pid))
    return Verdict()


def arc_distance(lon: float, point: float) -> float:
    """Кратчайшее расстояние по кругу между долготой и точкой, в градусах."""
    diff = abs((lon - point) % 360.0)
    return min(diff, 360.0 - diff)


def gandanta_distance(lon: float) -> float:
    """До ближайшего стыка воды и огня (0°, 120°, 240°)."""
    return min(arc_distance(lon, point) for point in C.GANDANTA_POINTS)


def sign_boundary_distance(lon: float) -> float:
    """До ближайшей границы знака. Знаки целые, поэтому границы кратны 30°."""
    return arc_distance(lon, round(lon / 30.0) * 30.0)


def lagna_gandanta(chart: PrashnaChart, _question: str) -> Verdict:
    if gandanta_distance(chart.asc_lon) <= C.GANDANTA_ORB:
        return Verdict(status=REJECT, reason=LAGNA_GANDANTA_REASON)
    return Verdict()


def lagna_bhava_sandhi(chart: PrashnaChart, _question: str) -> Verdict:
    """Проверяется после гандānты: там причина конкретнее, а точки те же."""
    if sign_boundary_distance(chart.asc_lon) <= C.BHAVA_SANDHI_ORB:
        return Verdict(status=REJECT, reason=LAGNA_SANDHI_REASON)
    return Verdict()


def moon_gandanta(chart: PrashnaChart, _question: str) -> Verdict:
    moon = chart.planets.get("Луна")
    if moon and gandanta_distance(moon.lon) <= C.GANDANTA_ORB:
        return Verdict(status=REJECT, reason=MOON_GANDANTA_REASON)
    return Verdict()


def kshina_chandra(chart: PrashnaChart, _question: str) -> Verdict:
    """Луна вблизи Солнца. Расстояние берём по кругу: 359° от Солнца — это 1°, а не 359°."""
    moon = chart.planets.get("Луна")
    sun = chart.planets.get("Солнце")
    if moon and sun and arc_distance(moon.lon, sun.lon) <= C.KSHINA_CHANDRA_ORB:
        return Verdict(status=REJECT, reason=KSHINA_CHANDRA_REASON)
    return Verdict()


# Правило — функция (карта, вопрос) → Verdict. Списки наполняются в C-02…C-05:
# порядок здесь и есть порядок проверки, от дешёвого к астрологическому.
Rule = Callable[[PrashnaChart, str], Verdict]
REJECT_RULES: list[Rule] = [
    no_clear_house,
    lagna_gandanta,
    lagna_bhava_sandhi,
    moon_gandanta,
    kshina_chandra,
]
CAUTION_RULES: list[Rule] = []


def check(
    chart: PrashnaChart, question: str, user_id: int, history: Sequence[PastAsk] = ()
) -> Verdict:
    """Собирает вердикт по включённым правилам. Первый `reject` прекращает разбор.

    `history` — прежние вопросы пользователя, которые достаёт вызывающий код:
    правилу повторного вопроса нужна БД, а модуль обязан оставаться чистым.
    Пустая история означает «не проверяем», а не «повторов не было».
    """
    repeat = repeated_question(chart, question, history)
    if repeat.rejected:
        return repeat

    cautions: list[str] = []
    for rule in REJECT_RULES:
        verdict = rule(chart, question)
        if verdict.rejected:
            return verdict
    for rule in CAUTION_RULES:
        verdict = rule(chart, question)
        if verdict.cautioned:
            cautions.append(verdict.reason)
    if cautions:
        return Verdict(status=CAUTION, reason=" ".join(cautions))
    return Verdict()
