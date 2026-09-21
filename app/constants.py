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
