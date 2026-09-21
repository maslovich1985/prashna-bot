"""Пригодна ли прашна к суждению (§5.5).

Классические признаки непригодной карты считает код, а не LLM: модуль остаётся
чистым — ни `db`, ни `llm`, ни aiogram, только карта и текст вопроса. Правила
добавляются по одному (C-02…C-05), здесь — каркас и общий вердикт.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from .chart import PrashnaChart

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


# Правило — функция (карта, вопрос) → Verdict. Списки наполняются в C-02…C-05:
# порядок здесь и есть порядок проверки, от дешёвого к астрологическому.
Rule = Callable[[PrashnaChart, str], Verdict]
REJECT_RULES: list[Rule] = []
CAUTION_RULES: list[Rule] = []


def check(chart: PrashnaChart, question: str, user_id: int) -> Verdict:
    """Собирает вердикт по включённым правилам. Первый `reject` прекращает разбор.

    `user_id` нужен правилу повторного вопроса (C-03): оно единственное смотрит
    на историю, и получит её через параметр, а не через импорт `db` — иначе модуль
    перестанет быть чистым.
    """
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
