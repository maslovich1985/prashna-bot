"""Конфигурация приложения. Все значения берутся из переменных окружения / .env."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent

load_dotenv(BASE_DIR / ".env")


def _int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, "") or default)
    except ValueError:
        return default


@dataclass(frozen=True)
class Settings:
    telegram_token: str = os.getenv("TELEGRAM_TOKEN", "")
    groq_api_key: str = os.getenv("GROQ_API_KEY", "")
    groq_model: str = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")
    groq_base_url: str = os.getenv("GROQ_BASE_URL", "https://api.groq.com/openai/v1")
    groq_timeout: int = _int("GROQ_TIMEOUT", 90)
    groq_max_tokens: int = _int("GROQ_MAX_TOKENS", 1800)
    # HTTPS-прокси для запросов к LLM, например http://user:pass@1.2.3.4:8080
    llm_proxy: str = os.getenv("LLM_PROXY", "")
    # Запасной адрес: ретраи бьют в тот же прокси и от его падения не спасают.
    llm_proxy_fallback: str = os.getenv("LLM_PROXY_FALLBACK", "")
    # Таймаут через прокси держим меньше GROQ_TIMEOUT, чтобы успеть переключиться
    # на запасной адрес, а не ждать всё окно на первом.
    llm_proxy_timeout: int = _int("LLM_PROXY_TIMEOUT", 55)
    # Через сколько секунд ожидания предупредить пользователя, что ответ идёт дольше обычного.
    llm_slow_notice: int = _int("LLM_SLOW_NOTICE", 25)

    db_path: Path = Path(os.getenv("DB_PATH", str(BASE_DIR / "data" / "prashna.sqlite3")))
    log_level: str = os.getenv("LOG_LEVEL", "INFO")

    # пустой DSN = Sentry выключен, бот работает как раньше
    sentry_dsn: str = os.getenv("SENTRY_DSN", "")
    sentry_env: str = os.getenv("SENTRY_ENV", "production")

    # антиспам
    daily_limit: int = _int("DAILY_LIMIT", 5)
    cooldown_seconds: int = _int("COOLDOWN_SECONDS", 30)
    admin_ids: tuple[int, ...] = field(
        default_factory=lambda: tuple(
            int(x) for x in os.getenv("ADMIN_IDS", "").replace(" ", "").split(",") if x
        )
    )

    # город по умолчанию, если пользователь не задал свой
    default_city: str = os.getenv("DEFAULT_CITY", "Москва")
    default_lat: float = float(os.getenv("DEFAULT_LAT", "55.7558"))
    default_lon: float = float(os.getenv("DEFAULT_LON", "37.6173"))
    default_tz: str = os.getenv("DEFAULT_TZ", "Europe/Moscow")

    geocoder_url: str = os.getenv("GEOCODER_URL", "https://nominatim.openstreetmap.org/search")
    geocoder_user_agent: str = os.getenv(
        "GEOCODER_UA", "prashna-bot/1.0 (contact: maslovichas1985@gmail.com)"
    )

    ayanamsa: str = os.getenv("AYANAMSA", "LAHIRI")

    # Контакт поддержки для /paysupport: @username или адрес почты.
    # Пусто — команда отвечает правилами возврата без контакта.
    support_contact: str = os.getenv("SUPPORT_CONTACT", "")


settings = Settings()


def validate() -> None:
    missing = []
    if not settings.telegram_token:
        missing.append("TELEGRAM_TOKEN")
    if not settings.groq_api_key:
        missing.append("GROQ_API_KEY")
    if missing:
        raise SystemExit(
            "Не заданы обязательные переменные окружения: "
            + ", ".join(missing)
            + "\nСкопируйте .env.example в .env и заполните его."
        )
