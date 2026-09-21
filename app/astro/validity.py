"""Пригодна ли прашна к суждению (§5.5).

Классические признаки непригодной карты считает код, а не LLM: модуль остаётся
чистым — ни `db`, ни `llm`, ни aiogram, только карта и текст вопроса. Правила
добавляются по одному (C-02…C-05), здесь — каркас и общий вердикт.
"""

from __future__ import annotations

import difflib
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from datetime import datetime, timedelta

from . import constants as C
from .chart import PrashnaChart, Sky
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


def check_question(question: str) -> Verdict:
    """Вопрос без ясного дома: ни одно ключевое слово не набрало `HOUSE_MIN_SCORE`.

    Карта для этого не нужна, поэтому проверку можно звать до резерва и до расчёта —
    ни кванта, ни CPU. `detect_house` иначе молча отдаёт дом 1, и толкование выходит
    уверенным, но ни о чём. Ждать тут нечего: помогает переформулировка, а не время,
    поэтому `retry_at` остаётся пустым.
    """
    _house, score = detect_house_scored(question)
    if score >= C.HOUSE_MIN_SCORE:
        return Verdict()
    return Verdict(status=REJECT, reason=NO_CLEAR_HOUSE_REASON)


def no_clear_house(_chart: PrashnaChart, question: str) -> Verdict:
    """То же правило в общем списке — на случай, если карту всё же посчитали."""
    return check_question(question)


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


# Правило — функция (карта, вопрос) → Verdict. Порядок в списках ниже и есть
# порядок проверки, от дешёвого к астрологическому.
Rule = Callable[[PrashnaChart, str], Verdict]


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


def geometry_reason(sky: Sky) -> str:
    """Причина отказа по геометрии или пустая строка.

    Одна функция на все геометрические правила: и полная карта, и дешёвый срез
    неба (C-06) проверяются одним кодом, иначе перебор моментов начнёт расходиться
    с настоящей проверкой. Порядок важен — гандānта конкретнее сандхи.
    """
    if gandanta_distance(sky.asc_lon) <= C.GANDANTA_ORB:
        return LAGNA_GANDANTA_REASON
    if sign_boundary_distance(sky.asc_lon) <= C.BHAVA_SANDHI_ORB:
        return LAGNA_SANDHI_REASON
    if gandanta_distance(sky.moon_lon) <= C.GANDANTA_ORB:
        return MOON_GANDANTA_REASON
    if arc_distance(sky.moon_lon, sky.sun_lon) <= C.KSHINA_CHANDRA_ORB:
        return KSHINA_CHANDRA_REASON
    return ""


# Причины, которые уходят со временем: лагна и Луна движутся. Для «нет ясного дома»
# и повторного вопроса срок бессмыслен — там помогает переформулировка или /chart.
GEOMETRY_REASONS = frozenset(
    {LAGNA_GANDANTA_REASON, LAGNA_SANDHI_REASON, MOON_GANDANTA_REASON, KSHINA_CHANDRA_REASON}
)


def sky_of(chart: PrashnaChart) -> Sky:
    moon = chart.planets.get("Луна")
    sun = chart.planets.get("Солнце")
    return Sky(
        asc_lon=chart.asc_lon, moon_lon=moon.lon if moon else 0.0, sun_lon=sun.lon if sun else 0.0
    )


def _geometry_rule(reason: str) -> Rule:
    """Оборачивает одну причину в правило: список правил остаётся читаемым перечнем."""

    def rule(chart: PrashnaChart, _question: str) -> Verdict:
        if geometry_reason(sky_of(chart)) == reason:
            return Verdict(status=REJECT, reason=reason)
        return Verdict()

    return rule


lagna_gandanta = _geometry_rule(LAGNA_GANDANTA_REASON)
lagna_bhava_sandhi = _geometry_rule(LAGNA_SANDHI_REASON)
moon_gandanta = _geometry_rule(MOON_GANDANTA_REASON)
kshina_chandra = _geometry_rule(KSHINA_CHANDRA_REASON)


def find_retry_moment(
    start: datetime,
    sky_at: Callable[[datetime], Sky],
    limit_minutes: int | None = None,
    step_minutes: int | None = None,
) -> datetime | None:
    """Первый момент после `start`, когда геометрия перестаёт мешать.

    Шагаем вперёд по минуте: лагна проходит градус примерно за четыре минуты,
    так что минутного шага хватает, чтобы не проскочить окно. `None` означает,
    что за `limit_minutes` просвета не нашлось — обещать срок в таком случае нечестно.
    """
    limit = C.RETRY_SEARCH_MINUTES if limit_minutes is None else limit_minutes
    step = C.RETRY_SEARCH_STEP_MINUTES if step_minutes is None else step_minutes
    for minute in range(step, limit + 1, step):
        moment = start + timedelta(minutes=minute)
        if not geometry_reason(sky_at(moment)):
            return moment
    return None


REJECT_RULES: list[Rule] = [
    no_clear_house,
    lagna_gandanta,
    lagna_bhava_sandhi,
    moon_gandanta,
    kshina_chandra,
]
CAUTION_RULES: list[Rule] = []


def check(
    chart: PrashnaChart,
    question: str,
    user_id: int,
    history: Sequence[PastAsk] = (),
    sky_at: Callable[[datetime], Sky] | None = None,
) -> Verdict:
    """Собирает вердикт по включённым правилам. Первый `reject` прекращает разбор.

    `history` — прежние вопросы пользователя, которые достаёт вызывающий код:
    правилу повторного вопроса нужна БД, а модуль обязан оставаться чистым.
    Пустая история означает «не проверяем», а не «повторов не было».

    `sky_at` — дешёвый срез неба на произвольный момент (`chart.sky_at`). Если он
    передан, к геометрическому отказу добавляется срок «когда попробовать снова»:
    отказ без срока бесполезен — пользователь ткнётся снова и решит, что бот сломан.
    """
    repeat = repeated_question(chart, question, history)
    if repeat.rejected:
        return repeat

    cautions: list[str] = []
    for rule in REJECT_RULES:
        verdict = rule(chart, question)
        if verdict.rejected:
            if sky_at is not None and verdict.reason in GEOMETRY_REASONS:
                return replace(verdict, retry_at=find_retry_moment(chart.when_utc, sky_at))
            return verdict
    for rule in CAUTION_RULES:
        verdict = rule(chart, question)
        if verdict.cautioned:
            cautions.append(verdict.reason)
    if cautions:
        return Verdict(status=CAUTION, reason=" ".join(cautions))
    return Verdict()
