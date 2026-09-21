"""Интерпретация прашна-карты через Groq (OpenAI-совместимый Chat Completions API)."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

import httpx

from .config import settings

log = logging.getLogger(__name__)

SYSTEM_PROMPT = """Ты — опытный джьотиши (ведический астролог), специалист по прашна-шастре
(хорарная ведическая астрология по школам «Прашна Марга» и «Прашна Таттва»).

Тебе передают ГОТОВУЮ, уже рассчитанную прашна-кундали на момент вопроса
(сидерический зодиак, аянамша Лахири, дома — целый знак) и список астрологических факторов.

ЖЁСТКИЕ ПРАВИЛА:
1. Никогда не пересчитывай и не «поправляй» положения планет — они даны точно. Опирайся
   только на переданные данные. Не выдумывай планеты, градусы, йоги или дома, которых нет в данных.
2. Рассуждай по правилам прашны: сила лагны и её владыки, владыка дома вопроса, Луна как
   ум вопрошающего, связь (соединение/аспект/обмен) между владыкой лагны и владыкой дома
   вопроса, благодетели и вредители в доме вопроса и в кендрах, аруда, положение в
   D9 (навамша) и D10 (дашамша), действующая вимшоттари-даша, панчанга.
3. Дай ОДНОЗНАЧНЫЙ вывод: «Да», «Скорее да», «Неопределённо», «Скорее нет» или «Нет» —
   и обязательно объясни, какими именно факторами карты он обоснован.
4. Укажи ориентировочные сроки, опираясь на качество знака лагны (чара — быстро,
   стхира — медленно, двисвабхава — средне/с повторами), скорость и накшатру значимых планет,
   владыку текущей антар-даши.
5. Пиши по-русски, спокойно, уважительно, без эзотерического пафоса и без запугивания.
   Никаких медицинских, юридических или финансовых предписаний — только астрологическое
   толкование как пища для размышления, решение принимает человек.

ФОРМАТ ОТВЕТА (обычный текст, без markdown-заголовков, до 350 слов):

Вердикт: <однозначный ответ>
Обоснование: 4–7 предложений с конкретными ссылками на факторы карты.
Сроки: <ориентировочный период и его астрологическое основание>
Что помогает: <1–2 фактора>
Что мешает: <1–2 фактора>
Совет: <одно практичное предложение>
"""


MIN_ANSWER_LEN = 200  # короче — это не толкование, а обрывок: не списываем


class LLMError(RuntimeError):
    """Толкование не получено. Каждый подкласс — отдельная строка таблицы §5.4.1."""


class LLMUnavailable(LLMError):
    """Прокси недоступен, Groq 5xx, обрыв соединения."""


class LLMRateLimited(LLMError):
    """429 не рассосался за все ретраи."""


class LLMAuthError(LLMError):
    """401/403: ключ протух, отозван или кончился биллинг. Ретраи бесполезны."""


class LLMTimeout(LLMError):
    """Ответ не пришёл за `GROQ_TIMEOUT`."""


class LLMEmptyAnswer(LLMError):
    """Пусто или короче `MIN_ANSWER_LEN`."""


@dataclass(frozen=True)
class Answer:
    text: str
    truncated: bool = False


def build_user_prompt(
    question: str, house: int, house_meaning: str, chart_text: str, factors: list[str]
) -> str:
    return (
        f"ВОПРОС ВОПРОШАЮЩЕГО:\n«{question}»\n\n"
        f"ДОМ ВОПРОСА: {house}-й ({house_meaning})\n\n"
        f"ПРАШНА-КУНДАЛИ:\n{chart_text}\n\n"
        f"ПРЕДВАРИТЕЛЬНЫЕ ФАКТОРЫ СУЖДЕНИЯ:\n"
        + "\n".join(f"- {f}" for f in factors)
        + "\n\nДай толкование строго в заданном формате."
    )


async def interpret(
    question: str,
    house: int,
    house_meaning: str,
    chart_text: str,
    factors: list[str],
    retries: int = 3,
) -> Answer:
    payload = {
        "model": settings.groq_model,
        "temperature": 0.4,
        "max_tokens": settings.groq_max_tokens,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {
                "role": "user",
                "content": build_user_prompt(question, house, house_meaning, chart_text, factors),
            },
        ],
    }
    headers = {
        "Authorization": f"Bearer {settings.groq_api_key}",
        "Content-Type": "application/json",
    }
    url = settings.groq_base_url.rstrip("/") + "/chat/completions"

    last_err: Exception | None = None
    rate_limited = False
    timed_out = False
    for attempt in range(retries):
        try:
            async with httpx.AsyncClient(
                timeout=settings.groq_timeout, proxy=settings.llm_proxy or None
            ) as client:
                r = await client.post(url, json=payload, headers=headers)
            if r.status_code in (401, 403):
                # Ключ сам не починится: ретраи только жгут время пользователя.
                raise LLMAuthError(f"Groq отверг ключ: HTTP {r.status_code}")
            if r.status_code == 429:
                rate_limited = True
                wait = float(r.headers.get("retry-after", 5))
                log.warning("Groq rate limit, ждём %.1f с", wait)
                await asyncio.sleep(min(wait, 20))
                continue
            r.raise_for_status()
            return _parse(r.json())
        except (LLMAuthError, LLMEmptyAnswer):
            raise
        except httpx.TimeoutException as e:
            timed_out = True
            last_err = e
            log.warning("Таймаут Groq (попытка %d)", attempt + 1)
            await asyncio.sleep(2 * (attempt + 1))
        except Exception as e:
            timed_out = False
            last_err = e
            log.warning("Ошибка запроса к Groq (попытка %d): %s", attempt + 1, e)
            await asyncio.sleep(2 * (attempt + 1))

    if timed_out:
        raise LLMTimeout(f"Groq не ответил вовремя: {last_err}")
    if rate_limited and last_err is None:
        raise LLMRateLimited("Groq вернул 429 на всех попытках")
    raise LLMUnavailable(f"Groq недоступен: {last_err}")


def _parse(data: dict) -> Answer:
    choice = data["choices"][0]
    text = (choice["message"]["content"] or "").strip()
    if len(text) < MIN_ANSWER_LEN:
        raise LLMEmptyAnswer(f"Ответ Groq короче {MIN_ANSWER_LEN} символов: {len(text)}")
    # finish_reason=length — ответ упёрся в groq_max_tokens: он есть, но оборван.
    return Answer(text=text, truncated=choice.get("finish_reason") == "length")
