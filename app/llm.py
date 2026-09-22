"""Интерпретация прашна-карты через Groq (OpenAI-совместимый Chat Completions API)."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

import httpx

from . import observability
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


def proxy_chain() -> list[str | None]:
    """Адреса, через которые пробуем ходить, по порядку.

    Ретраи бьют в тот же адрес и от упавшего прокси не спасают — поэтому список,
    а не один адрес. Пусто = идём напрямую.
    """
    chain: list[str | None] = [p for p in (settings.llm_proxy, settings.llm_proxy_fallback) if p]
    return chain or [None]


def timeout_for(proxy: str | None) -> float:
    """Таймаут запроса. Через прокси он короче `GROQ_TIMEOUT`: смысл в том, чтобы
    успеть переключиться на запасной адрес, а не ждать всё окно на первом."""
    if proxy is None:
        return float(settings.groq_timeout)
    return float(min(settings.llm_proxy_timeout, settings.groq_timeout))


# --- счётчики сбоев (H-02) -------------------------------------------------- #

# Кто виноват: сам прокси или Groq за ним. Без разделения в логах видно только
# «LLM недоступен», и чинить начинают не то.
PROXY = "прокси"
GROQ = "groq"

# Тип сбоя. 429 и 5xx приходят уже от Groq, поэтому у прокси их не бывает.
TIMEOUT = "таймаут"
CONNECT = "соединение"
RATE_LIMIT = "429"
SERVER = "5xx"
OTHER = "прочее"

_failures: dict[tuple[str, str], int] = {}


def note_failure(source: str, kind: str) -> None:
    _failures[(source, kind)] = _failures.get((source, kind), 0) + 1
    observability.tag_llm_failure(source, kind)


def failures() -> dict[str, int]:
    """Снимок счётчиков: «прокси/таймаут» → сколько раз. Живёт в памяти процесса."""
    return {f"{source}/{kind}": n for (source, kind), n in sorted(_failures.items())}


def reset_failures() -> None:
    """Нужен тестам; в боте счётчики обнуляет только рестарт."""
    _failures.clear()


def classify(error: Exception, proxy: str | None) -> tuple[str, str]:
    """Кому выставлять счёт за сбой.

    Таймаут и обрыв через прокси пишем на прокси: его окно короче `GROQ_TIMEOUT`,
    и до Groq запрос мог вовсе не дойти. Без прокси виноват сам Groq.
    """
    source = PROXY if proxy is not None else GROQ
    if isinstance(error, httpx.ProxyError):
        return PROXY, CONNECT
    if isinstance(error, httpx.TimeoutException):
        return source, TIMEOUT
    if isinstance(error, httpx.TransportError):
        return source, CONNECT
    if isinstance(error, httpx.HTTPStatusError):
        # Ответ пришёл — значит, прокси отработал, а ругается Groq. Сам объект
        # ответа может быть не заполнен (стабы в тестах), поэтому через getattr.
        status = getattr(getattr(error, "response", None), "status_code", 0)
        return GROQ, SERVER if status >= 500 else OTHER
    return source, OTHER


async def health_check() -> bool:
    """Пинг при старте: жив ли путь до Groq. Бота не валит — карта считается и без LLM."""
    url = settings.groq_base_url.rstrip("/") + "/models"
    headers = {"Authorization": f"Bearer {settings.groq_api_key}"}
    for proxy in proxy_chain():
        try:
            async with httpx.AsyncClient(timeout=10, proxy=proxy) as client:
                r = await client.get(url, headers=headers)
            if r.status_code < 400:
                log.info("LLM доступен через %s", proxy or "прямое соединение")
                return True
            log.warning(
                "LLM через %s отвечает HTTP %s", proxy or "прямое соединение", r.status_code
            )
        except Exception as e:
            source, kind = classify(e, proxy)
            note_failure(source, kind)
            log.warning("LLM недоступен через %s: %s", proxy or "прямое соединение", e)
    log.warning("Ни один канал до Groq не отвечает: толкования не будут выдаваться")
    return False


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
    chain = proxy_chain()
    for attempt in range(retries):
        # Внутри попытки перебираем адреса: упавший прокси должен стоить одного
        # запроса, а не всех трёх.
        proxy = chain[attempt % len(chain)]
        try:
            async with httpx.AsyncClient(timeout=timeout_for(proxy), proxy=proxy) as client:
                r = await client.post(url, json=payload, headers=headers)
            if r.status_code in (401, 403):
                # Ключ сам не починится: ретраи только жгут время пользователя.
                raise LLMAuthError(f"Groq отверг ключ: HTTP {r.status_code}")
            if r.status_code == 429:
                rate_limited = True
                note_failure(GROQ, RATE_LIMIT)
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
            note_failure(*classify(e, proxy))
            log.warning("Таймаут через %s (попытка %d)", proxy or "прямое соединение", attempt + 1)
            await _backoff(attempt, chain)
        except Exception as e:
            timed_out = False
            last_err = e
            note_failure(*classify(e, proxy))
            log.warning(
                "Ошибка запроса через %s (попытка %d): %s",
                proxy or "прямое соединение",
                attempt + 1,
                e,
            )
            await _backoff(attempt, chain)

    if timed_out:
        raise LLMTimeout(f"Groq не ответил вовремя: {last_err}")
    if rate_limited and last_err is None:
        raise LLMRateLimited("Groq вернул 429 на всех попытках")
    raise LLMUnavailable(f"Groq недоступен: {last_err}")


async def _backoff(attempt: int, chain: list[str | None]) -> None:
    """Пауза перед следующей попыткой. Если следующий адрес другой, ждать нечего:
    паузу имеет смысл держать только перед повтором в тот же адрес."""
    if len(chain) > 1:
        return
    await asyncio.sleep(2 * (attempt + 1))


def _parse(data: dict) -> Answer:
    choice = data["choices"][0]
    text = (choice["message"]["content"] or "").strip()
    if len(text) < MIN_ANSWER_LEN:
        raise LLMEmptyAnswer(f"Ответ Groq короче {MIN_ANSWER_LEN} символов: {len(text)}")
    # finish_reason=length — ответ упёрся в groq_max_tokens: он есть, но оборван.
    return Answer(text=text, truncated=choice.get("finish_reason") == "length")
