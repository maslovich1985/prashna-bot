"""SQLite: профили пользователей (город/координаты), история прашн, счётчики лимитов."""

from __future__ import annotations

import logging
import sqlite3
import subprocess
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import settings

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
    user_id     INTEGER NOT NULL,
    day         TEXT NOT NULL,
    count       INTEGER NOT NULL DEFAULT 0,
    last_at     TEXT,
    PRIMARY KEY (user_id, day)
);

CREATE TABLE IF NOT EXISTS geocache (
    query       TEXT PRIMARY KEY,
    place       TEXT,
    lat         REAL,
    lon         REAL,
    tz          TEXT
);
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


# Каждый элемент — один шаг, применяемый ровно один раз; индекс i соответствует
# user_version = i + 1. Шаги только дописывают схему: откат кода не должен оставлять
# базу нечитаемой для предыдущей версии. Менять уже выпущенный шаг нельзя — на серверах,
# где он применён, правка не выполнится; нужен новый шаг в конце списка.
MIGRATIONS: list[str] = []


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
        for i, sql in enumerate(pending, start=version):
            log.info("Миграция %d → %d", i, i + 1)
            c.executescript(sql)
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


def check_and_bump(user_id: int, daily_limit: int, cooldown: int) -> tuple[bool, str]:
    """Возвращает (разрешено, причина отказа)."""
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    now = datetime.now(timezone.utc)
    with conn() as c:
        row = c.execute(
            "SELECT count, last_at FROM usage WHERE user_id=? AND day=?", (user_id, today)
        ).fetchone()
        count = row["count"] if row else 0
        if row and row["last_at"]:
            delta = (now - datetime.fromisoformat(row["last_at"])).total_seconds()
            if delta < cooldown:
                return False, f"Подождите ещё {int(cooldown - delta)} с перед следующим вопросом."
        if count >= daily_limit:
            return False, (
                f"Дневной лимит исчерпан ({daily_limit} прашн в сутки). "
                "Прашна требует искреннего, вызревшего вопроса — вернитесь завтра."
            )
        c.execute(
            "INSERT INTO usage (user_id, day, count, last_at) VALUES (?,?,1,?) "
            "ON CONFLICT(user_id, day) DO UPDATE SET "
            "count = usage.count + 1, last_at = excluded.last_at",
            (user_id, today, now.isoformat()),
        )
    return True, ""


def remaining(user_id: int, daily_limit: int) -> int:
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    with conn() as c:
        row = c.execute(
            "SELECT count FROM usage WHERE user_id=? AND day=?", (user_id, today)
        ).fetchone()
    return max(0, daily_limit - (row["count"] if row else 0))


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
