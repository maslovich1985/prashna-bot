"""Тарифы и квоты. Меняются кодом через ревью, а не в БД."""

from __future__ import annotations

from dataclasses import dataclass

TRIAL_QUESTIONS = 2
REFERRAL_BONUS = 2
REFERRAL_MAX = 10


@dataclass(frozen=True)
class Plan:
    """Тариф.

    Подписка (`days > 0`) даёт `daily_limit` вопросов в сутки на срок `days`.
    Разовый пакет (`days == 0`) даёт `questions` вопросов без срока.
    """

    title: str
    days: int
    daily_limit: int = 0
    questions: int = 0
    # Цена в звёздах (XTR) — открытый вопрос ROADMAP §15.1: считается по комиссии
    # Telegram ~30% и стоимости токенов Groq на ответ. None = тариф не продаётся,
    # инвойс по нему выставлять нельзя.
    stars: int | None = None

    @property
    def is_subscription(self) -> bool:
        return self.days > 0

    @property
    def sellable(self) -> bool:
        return self.stars is not None


PLANS: dict[str, Plan] = {
    "month": Plan(title="Подписка на месяц", days=30, daily_limit=10),
    "year": Plan(title="Подписка на год", days=365, daily_limit=10),
    "pack10": Plan(title="10 вопросов", days=0, questions=10),
}


@dataclass(frozen=True)
class City:
    """Город из быстрого списка: координаты и tz зашиты, геокодер не нужен."""

    name: str
    lat: float
    lon: float
    tz: str


# Десять самых населённых городов России. Ключ уезжает в callback_data, поэтому
# он латиницей и коротким. Координаты — центр города; для лагны этой точности
# достаточно: разница в пару километров двигает асцендент на доли минуты дуги.
CITIES: dict[str, City] = {
    "msk": City("Москва", 55.7558, 37.6173, "Europe/Moscow"),
    "spb": City("Санкт-Петербург", 59.9311, 30.3609, "Europe/Moscow"),
    "nsk": City("Новосибирск", 55.0084, 82.9357, "Asia/Novosibirsk"),
    "ekb": City("Екатеринбург", 56.8389, 60.6057, "Asia/Yekaterinburg"),
    "kzn": City("Казань", 55.7963, 49.1088, "Europe/Moscow"),
    "nnv": City("Нижний Новгород", 56.3269, 44.0059, "Europe/Moscow"),
    "chl": City("Челябинск", 55.1644, 61.4368, "Asia/Yekaterinburg"),
    "kya": City("Красноярск", 56.0153, 92.8932, "Asia/Krasnoyarsk"),
    "sam": City("Самара", 53.1959, 50.1002, "Europe/Samara"),
    "ufa": City("Уфа", 54.7388, 55.9721, "Asia/Yekaterinburg"),
}


# Ориентир для экрана продажи: сколько стоит тот же вопрос у живого астролога.
# Цифры не выдуманы — снято 22.09.2026 с трёх площадок:
#   astrou.ru/vopros — 18 000 ₽ за хорарный вопрос;
#   skidkom.ru, астролог «Бина» — 8 000 ₽;
#   mylablife.ru — обзор рынка, 3 000–6 000 ₽ с подготовкой.
# Берём нижнюю границу диапазона: завышать сравнение нечестно, а занижать — не нужно.
CONSULT_PRICE_MIN = 3000
CONSULT_PRICE_MAX = 8000
