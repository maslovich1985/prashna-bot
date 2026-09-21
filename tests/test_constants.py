from dataclasses import FrozenInstanceError, is_dataclass

import pytest

from app.constants import PLANS, REFERRAL_BONUS, REFERRAL_MAX, TRIAL_QUESTIONS, Plan


def test_quotas():
    assert (TRIAL_QUESTIONS, REFERRAL_BONUS, REFERRAL_MAX) == (2, 2, 10)


def test_plan_is_frozen_dataclass():
    assert is_dataclass(Plan)
    with pytest.raises(FrozenInstanceError):
        PLANS["month"].stars = 100


def test_plans_shape():
    assert set(PLANS) == {"month", "year", "pack10"}
    for plan in PLANS.values():
        assert isinstance(plan, Plan)
        assert plan.title
        # Подписка живёт сроком и суточным лимитом, пакет — числом вопросов.
        if plan.is_subscription:
            assert plan.daily_limit > 0 and plan.questions == 0
        else:
            assert plan.questions > 0 and plan.daily_limit == 0


def test_prices_are_placeholders_until_roadmap_15_1():
    # Цены в звёздах ещё не решены (§15.1). Тест падает, когда их проставят,
    # — это сигнал снять заглушку вместе с ним, а не деплоить наугад.
    assert all(p.stars is None and not p.sellable for p in PLANS.values())
