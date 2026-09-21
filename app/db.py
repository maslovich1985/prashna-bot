"""SQLite: профили пользователей (город/координаты), история прашн, счётчики лимитов."""

from __future__ import annotations

import logging
import sqlite3
import subprocess
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from . import texts
from .config import settings
from .constants import PLANS, TRIAL_QUESTIONS

log = logging.getLogger(__name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    user_id     INTEGER PRIMARY KEY,
    username    TEXT,
    place       TEXT,
    lat         REAL,
    lon         REAL,
    tz          TEXT,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS prashna (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     INTEGER NOT NULL,
    asked_at    TEXT NOT NULL,
    place       TEXT,
    question    TEXT NOT NULL,
    house       INTEGER,
    chart_text  TEXT,
    answer      TEXT
);
CREATE INDEX IF NOT EXISTS idx_prashna_user ON prashna(user_id, asked_at DESC);

CREATE TABLE IF NOT EXISTS usage (
    user_id      INTEGER NOT NULL,
    day          TEXT NOT NULL,
    count        INTEGER NOT NULL DEFAULT 0,
    last_at      TEXT,
    -- Открытый резерв: momент списания и источник права, за счёт которого списали.
    -- NULL = вопрос закрыт (commit) или квант уже возвращён (release).
    reserved_at  TEXT,
    reserved_src TEXT,
    PRIMARY KEY (user_id, day)
);

CREATE TABLE IF NOT EXISTS geocache (
    query       TEXT PRIMARY KEY,
    place       TEXT,
    lat         REAL,
    lon         REAL,
    tz          TEXT
);

CREATE TABLE IF NOT EXISTS entitlements (
    user_id        INTEGER PRIMARY KEY,
    plan           TEXT,
    expires_at     TEXT,
    questions_left INTEGER NOT NULL DEFAULT 0,
    trial_used     INTEGER NOT NULL DEFAULT 0,
    updated_at     TEXT NOT NULL
);

-- charge_id — telegram_payment_charge_id. PRIMARY KEY здесь не украшение,
-- а защита от двойной выдачи: повторный successful_payment с тем же id
-- не создаст вторую запись и не начислит доступ дважды.
CREATE TABLE IF NOT EXISTS payments (
    charge_id   TEXT PRIMARY KEY,
    user_id     INTEGER NOT NULL,
    plan        TEXT NOT NULL,
    stars       INTEGER NOT NULL,
    paid_at     TEXT NOT NULL,
    refunded_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_payments_user ON payments(user_id, paid_at DESC);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@contextmanager
def conn() -> Iterator[sqlite3.Connection]:
    path = Path(settings.db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(path, timeout=15)
    c.row_factory = sqlite3.Row
    try:
        c.execute("PRAGMA journal_mode=WAL")
        yield c
        c.commit()
    finally:
        c.close()


def _add_reserve_columns(c: sqlite3.Connection) -> None:
    """ALTER TABLE ... ADD COLUMN не умеет IF NOT EXISTS, поэтому смотрим схему сами."""
    have = {r["name"] for r in c.execute("PRAGMA table_info(usage)")}
    for column in ("reserved_at", "reserved_src"):
        if column not in have:
            c.execute(f"ALTER TABLE usage ADD COLUMN {column} TEXT")


# Каждый элемент — один шаг, применяемый ровно один раз; индекс i соответствует
# user_version = i + 1. Шаги только дописывают схему: откат кода не должен оставлять
# базу нечитаемой для предыдущей версии. Менять уже выпущенный шаг нельзя — на серверах,
# где он применён, правка не выполнится; нужен новый шаг в конце списка.
MIGRATIONS: list[str | Callable[[sqlite3.Connection], None]] = [
    # 0 → 1: таблицы квот и платежей (B-01). Только CREATE TABLE IF NOT EXISTS —
    # существующие данные не трогаются, прошлая версия кода такую базу ещё читает.
    """
CREATE TABLE IF NOT EXISTS entitlements (
    user_id        INTEGER PRIMARY KEY,
    plan           TEXT,
    expires_at     TEXT,
    questions_left INTEGER NOT NULL DEFAULT 0,
    trial_used     INTEGER NOT NULL DEFAULT 0,
    updated_at     TEXT NOT NULL
);

-- charge_id — telegram_payment_charge_id. PRIMARY KEY здесь не украшение,
-- а защита от двойной выдачи: повторный successful_payment с тем же id
-- не создаст вторую запись и не начислит доступ дважды.
CREATE TABLE IF NOT EXISTS payments (
    charge_id   TEXT PRIMARY KEY,
    user_id     INTEGER NOT NULL,
    plan        TEXT NOT NULL,
    stars       INTEGER NOT NULL,
    paid_at     TEXT NOT NULL,
    refunded_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_payments_user ON payments(user_id, paid_at DESC);
""",
    # 1 → 2: открытый резерв в usage (B-06). Колонки добавляются пустыми, старый код
    # их не замечает: он писал в usage по именам, а не по SELECT *.
    _add_reserve_columns,
]


def _backup_before_migrate() -> None:
    """Снимает бэкап перед изменением схемы. Нет скрипта — просто предупреждение."""
    script = Path(__file__).resolve().parent.parent / "deploy" / "backup.sh"
    if not script.exists():
        log.warning("Миграции без бэкапа: нет %s", script)
        return
    try:
        subprocess.run(["bash", str(script)], check=True, capture_output=True, timeout=300)
    except Exception as e:
        # Миграция необратима, а откат кода вернёт старую версию к уехавшей схеме.
        # Без свежей копии дешевле не стартовать вовсе.
        raise RuntimeError(f"Бэкап перед миграцией не удался: {e}") from e


def migrate() -> None:
    """Догоняет схему до последней версии. Идемпотентна: применяет только новые шаги."""
    with conn() as c:
        version = int(c.execute("PRAGMA user_version").fetchone()[0])
        pending = MIGRATIONS[version:]
        if not pending:
            return

    _backup_before_migrate()

    with conn() as c:
        for i, step in enumerate(pending, start=version):
            log.info("Миграция %d → %d", i, i + 1)
            # Шаг — либо SQL-скрипт, либо функция: чистым SQL идемпотентно добавить
            # колонку нельзя, а шаг обязан переживать повторный запуск.
            if callable(step):
                step(c)
            else:
                c.executescript(step)
            c.execute(f"PRAGMA user_version = {i + 1}")


def init() -> None:
    with conn() as c:
        # SCHEMA описывает актуальную схему целиком, поэтому на пустой базе миграции
        # уже «содержатся» в ней — применять их поверх значило бы дублировать колонки.
        fresh = (
            c.execute("SELECT COUNT(*) FROM sqlite_master WHERE type = 'table'").fetchone()[0] == 0
        )
        c.executescript(SCHEMA)
        if fresh:
            c.execute(f"PRAGMA user_version = {len(MIGRATIONS)}")
    migrate()


# ----------------------------- пользователи ------------------------------- #


def get_user(user_id: int) -> dict[str, Any] | None:
    with conn() as c:
        row = c.execute("SELECT * FROM users WHERE user_id = ?", (user_id,)).fetchone()
        return dict(row) if row else None


def upsert_user(user_id: int, username: str | None = None, **fields: Any) -> None:
    with conn() as c:
        c.execute(
            "INSERT INTO users (user_id, username, created_at, updated_at) VALUES (?,?,?,?) "
            "ON CONFLICT(user_id) DO UPDATE SET "
            "username=COALESCE(excluded.username, users.username), "
            "updated_at=excluded.updated_at",
            (user_id, username, _now(), _now()),
        )
        if fields:
            cols = ", ".join(f"{k} = ?" for k in fields)
            c.execute(
                f"UPDATE users SET {cols}, updated_at = ? WHERE user_id = ?",
                (*fields.values(), _now(), user_id),
            )


# -------------------------------- лимиты ---------------------------------- #


def remaining(user_id: int, daily_limit: int) -> int:
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    with conn() as c:
        row = c.execute(
            "SELECT count FROM usage WHERE user_id=? AND day=?", (user_id, today)
        ).fetchone()
    return max(0, daily_limit - (row["count"] if row else 0))


# ------------------------------- права ------------------------------------ #


@dataclass(frozen=True)
class Entitlement:
    """Чем именно пользователь платит за следующий вопрос (§5.1).

    `source`: admin | subscription | questions | trial | none.
    `daily_limit` = 0 — суточного потолка нет: у пакета и пробных расход
    считается квантами `left`, а не сутками.
    """

    source: str
    daily_limit: int = 0
    left: int = 0
    plan: str | None = None
    expires_at: str | None = None

    @property
    def allowed(self) -> bool:
        return self.source != "none"

    @property
    def unlimited(self) -> bool:
        return self.source == "admin"


def get_entitlement(user_id: int) -> dict[str, Any] | None:
    with conn() as c:
        row = c.execute("SELECT * FROM entitlements WHERE user_id = ?", (user_id,)).fetchone()
        return dict(row) if row else None


def entitlement_for(user_id: int, now: datetime | None = None) -> Entitlement:
    """Разрешение прав по приоритету §5.1: admin → подписка → пакет → пробные → отказ.

    Срок подписки проверяется лениво, прямо здесь: планировщика, который гасил бы
    истёкшие подписки, нет и не нужно.
    """
    if user_id in settings.admin_ids:
        return Entitlement(source="admin")

    row = get_entitlement(user_id) or {}
    plan_key = row.get("plan")
    plan = PLANS.get(plan_key) if plan_key else None
    expires_at = row.get("expires_at")

    if plan and plan.is_subscription and expires_at:
        moment = now or datetime.now(timezone.utc)
        if datetime.fromisoformat(expires_at) > moment:
            return Entitlement(
                source="subscription",
                daily_limit=plan.daily_limit,
                left=remaining(user_id, plan.daily_limit),
                plan=plan_key,
                expires_at=expires_at,
            )

    questions_left = int(row.get("questions_left") or 0)
    if questions_left > 0:
        return Entitlement(source="questions", left=questions_left, plan=plan_key)

    # Пробные пожизненные, а не суточные: иначе подписка никому не нужна.
    trial_left = TRIAL_QUESTIONS - int(row.get("trial_used") or 0)
    if trial_left > 0:
        return Entitlement(source="trial", left=trial_left)

    return Entitlement(source="none")


# ------------------------- резерв кванта вопроса --------------------------- #


RESERVE_TTL = 300  # с; дольше вопрос не обрабатывается — карта и LLM укладываются в минуты


@dataclass(frozen=True)
class Reservation:
    """Квант, списанный под один вопрос. Возвращается `release`, закрепляется `commit`."""

    user_id: int
    source: str
    day: str | None = None


def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def reserve(user_id: int, cooldown: int) -> tuple[Reservation | None, str]:
    """Проверяет право и списывает квант авансом. Возвращает (резерв, причина отказа).

    Списываем до ответа, а не после: иначе параллельные вопросы обходят лимит.
    Вернуть квант — задача `release`.
    """
    ent = entitlement_for(user_id)
    if ent.source == "admin":
        return Reservation(user_id=user_id, source="admin"), ""
    if not ent.allowed:
        return None, texts.NO_ENTITLEMENT

    day = _today()
    now = datetime.now(timezone.utc)
    with conn() as c:
        row = c.execute(
            "SELECT count, last_at FROM usage WHERE user_id=? AND day=?", (user_id, day)
        ).fetchone()
        if row and row["last_at"]:
            delta = (now - datetime.fromisoformat(row["last_at"])).total_seconds()
            if delta < cooldown:
                return None, texts.cooldown_wait(int(cooldown - delta))
        # Суточный потолок есть только у подписки: пакет и пробные считаются квантами.
        if ent.source == "subscription" and (row["count"] if row else 0) >= ent.daily_limit:
            return None, texts.daily_limit_reached(ent.daily_limit)

        c.execute(
            "INSERT INTO usage (user_id, day, count, last_at, reserved_at, reserved_src) "
            "VALUES (?,?,1,?,?,?) "
            "ON CONFLICT(user_id, day) DO UPDATE SET "
            "count = usage.count + 1, last_at = excluded.last_at, "
            "reserved_at = excluded.reserved_at, reserved_src = excluded.reserved_src",
            (user_id, day, now.isoformat(), now.isoformat(), ent.source),
        )
        if ent.source == "questions":
            c.execute(
                "UPDATE entitlements SET questions_left = questions_left - 1, updated_at = ? "
                "WHERE user_id = ? AND questions_left > 0",
                (_now(), user_id),
            )
        elif ent.source == "trial":
            c.execute(
                "INSERT INTO entitlements (user_id, trial_used, updated_at) VALUES (?,1,?) "
                "ON CONFLICT(user_id) DO UPDATE SET "
                "trial_used = entitlements.trial_used + 1, updated_at = excluded.updated_at",
                (user_id, _now()),
            )
    return Reservation(user_id=user_id, source=ent.source, day=day), ""


def commit(res: Reservation) -> None:
    """Закрепляет резерв: квант списан ещё в `reserve`, здесь снимается пометка.

    Без неё рестарт-сборщик (`release_stale`) вернул бы уже отработанный вопрос.
    """
    if res.source == "admin":
        return
    with conn() as c:
        c.execute(
            "UPDATE usage SET reserved_at = NULL, reserved_src = NULL WHERE user_id=? AND day=?",
            (res.user_id, res.day),
        )


def release(res: Reservation) -> None:
    """Возвращает неиспользованный квант. Идемпотентности не требуется: вызов один."""
    if res.source == "admin":
        return
    with conn() as c:
        c.execute(
            "UPDATE usage SET count = MAX(count - 1, 0), reserved_at = NULL, "
            "reserved_src = NULL WHERE user_id=? AND day=?",
            (res.user_id, res.day),
        )
        if res.source == "questions":
            c.execute(
                "UPDATE entitlements SET questions_left = questions_left + 1, updated_at = ? "
                "WHERE user_id = ?",
                (_now(), res.user_id),
            )
        elif res.source == "trial":
            c.execute(
                "UPDATE entitlements SET trial_used = MAX(trial_used - 1, 0), updated_at = ? "
                "WHERE user_id = ?",
                (_now(), res.user_id),
            )


def release_stale(now: datetime | None = None) -> int:
    """Возвращает кванты по резервам старше `RESERVE_TTL`. Вызывается один раз при старте.

    Рестарт между `reserve` и `commit` (деплой, OOM) иначе съедает вопрос молча:
    закрепить его уже некому.
    """
    moment = now or datetime.now(timezone.utc)
    cutoff = (moment - timedelta(seconds=RESERVE_TTL)).isoformat()
    with conn() as c:
        rows = c.execute(
            "SELECT user_id, day, reserved_src FROM usage "
            "WHERE reserved_at IS NOT NULL AND reserved_at < ?",
            (cutoff,),
        ).fetchall()
    for row in rows:
        release(
            Reservation(
                user_id=row["user_id"], source=row["reserved_src"] or "subscription", day=row["day"]
            )
        )
    if rows:
        log.info("Возвращено висящих резервов: %d", len(rows))
    return len(rows)


# ------------------------------- история ---------------------------------- #


def save_prashna(
    user_id: int, question: str, house: int, place: str, chart_text: str, answer: str
) -> int:
    with conn() as c:
        cur = c.execute(
            "INSERT INTO prashna (user_id, asked_at, place, question, house, chart_text, answer) "
            "VALUES (?,?,?,?,?,?,?)",
            (user_id, _now(), place, question, house, chart_text, answer),
        )
        return int(cur.lastrowid)


def history(user_id: int, limit: int = 10) -> list[dict[str, Any]]:
    with conn() as c:
        rows = c.execute(
            "SELECT id, asked_at, question, house FROM prashna WHERE user_id=? "
            "ORDER BY asked_at DESC LIMIT ?",
            (user_id, limit),
        ).fetchall()
    return [dict(r) for r in rows]


def get_prashna(user_id: int, pid: int) -> dict[str, Any] | None:
    with conn() as c:
        row = c.execute("SELECT * FROM prashna WHERE id=? AND user_id=?", (pid, user_id)).fetchone()
    return dict(row) if row else None


def clear_history(user_id: int) -> int:
    with conn() as c:
        cur = c.execute("DELETE FROM prashna WHERE user_id=?", (user_id,))
        return cur.rowcount


# ------------------------------- геокэш ----------------------------------- #


def geocache_get(query: str) -> dict[str, Any] | None:
    with conn() as c:
        row = c.execute("SELECT * FROM geocache WHERE query=?", (query.lower().strip(),)).fetchone()
    return dict(row) if row else None


def geocache_put(query: str, place: str, lat: float, lon: float, tz: str) -> None:
    with conn() as c:
        c.execute(
            "INSERT OR REPLACE INTO geocache (query, place, lat, lon, tz) VALUES (?,?,?,?,?)",
            (query.lower().strip(), place, lat, lon, tz),
        )


def stats() -> dict[str, int]:
    with conn() as c:
        users = c.execute("SELECT COUNT(*) n FROM users").fetchone()["n"]
        total = c.execute("SELECT COUNT(*) n FROM prashna").fetchone()["n"]
        today = c.execute(
            "SELECT COALESCE(SUM(count),0) n FROM usage WHERE day=?",
            (datetime.now(timezone.utc).strftime("%Y-%m-%d"),),
        ).fetchone()["n"]
    return {"пользователей": users, "всего прашн": total, "сегодня": today}
