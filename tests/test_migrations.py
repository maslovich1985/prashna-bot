"""Механизм миграций: user_version, догон старой базы, идемпотентность."""

from __future__ import annotations

from pathlib import Path

import pytest

from app import db

ADD_COLUMN = "ALTER TABLE users ADD COLUMN referrer_id INTEGER"
ADD_TABLE = "CREATE TABLE IF NOT EXISTS demo (id INTEGER PRIMARY KEY)"

# Реальные шаги уже применены к базе из фикстуры: свои проверки строим поверх них,
# иначе тест начнёт падать при каждом новом шаге в MIGRATIONS.
BASE = len(db.MIGRATIONS)


def _user_version() -> int:
    with db.conn() as c:
        return int(c.execute("PRAGMA user_version").fetchone()[0])


def _columns(table: str) -> set[str]:
    with db.conn() as c:
        return {r["name"] for r in c.execute(f"PRAGMA table_info({table})")}


@pytest.fixture(autouse=True)
def no_backup(monkeypatch: pytest.MonkeyPatch) -> None:
    """backup.sh дёргает sqlite3 на боевых путях — в тестах он не нужен."""
    monkeypatch.setattr(db, "_backup_before_migrate", lambda: None)


def test_fresh_db_is_marked_as_current(monkeypatch: pytest.MonkeyPatch, db_path: Path) -> None:
    # Пустая база создаётся из SCHEMA, где миграции уже учтены: применять их
    # поверх — дублировать колонки.
    monkeypatch.setattr(db, "MIGRATIONS", [ADD_COLUMN, ADD_TABLE])
    db_path.unlink()
    db.init()
    assert _user_version() == 2
    assert "referrer_id" not in _columns("users")  # SCHEMA сама по себе колонку не добавляет


def test_old_db_gets_new_column(monkeypatch: pytest.MonkeyPatch) -> None:
    assert _user_version() == BASE  # фикстура уже догнала базу до текущей версии
    monkeypatch.setattr(db, "MIGRATIONS", [*db.MIGRATIONS, ADD_COLUMN])

    db.init()

    assert "referrer_id" in _columns("users")
    assert _user_version() == BASE + 1


def test_data_survives_migration(monkeypatch: pytest.MonkeyPatch) -> None:
    db.upsert_user(1, "tester")
    monkeypatch.setattr(db, "MIGRATIONS", [*db.MIGRATIONS, ADD_COLUMN])

    db.init()

    user = db.get_user(1)
    assert user["username"] == "tester"
    assert user["referrer_id"] is None


def test_second_init_is_noop(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(db, "MIGRATIONS", [*db.MIGRATIONS, ADD_COLUMN])
    db.init()
    db.init()  # повторный ALTER упал бы с duplicate column
    assert _user_version() == BASE + 1


def test_only_pending_steps_apply(monkeypatch: pytest.MonkeyPatch) -> None:
    released = list(db.MIGRATIONS)
    monkeypatch.setattr(db, "MIGRATIONS", [*released, ADD_COLUMN])
    db.init()
    assert _user_version() == BASE + 1

    monkeypatch.setattr(db, "MIGRATIONS", [*released, ADD_COLUMN, ADD_TABLE])
    db.init()

    assert _user_version() == BASE + 2
    with db.conn() as c:
        assert c.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='demo'"
        ).fetchone()


def test_backup_failure_stops_migration(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(db, "MIGRATIONS", [*db.MIGRATIONS, ADD_COLUMN])
    monkeypatch.setattr(
        db, "_backup_before_migrate", lambda: (_ for _ in ()).throw(RuntimeError("нет копии"))
    )

    with pytest.raises(RuntimeError):
        db.init()

    # Схема не тронута: без свежей копии откат кода остался бы с уехавшей схемой
    assert "referrer_id" not in _columns("users")
    assert _user_version() == BASE


def test_no_backup_when_nothing_to_apply(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[int] = []
    monkeypatch.setattr(db, "_backup_before_migrate", lambda: calls.append(1))

    db.init()  # все шаги уже применены — бэкап дёргать не за чем

    assert calls == []


def test_reserve_columns_step_is_idempotent(monkeypatch: pytest.MonkeyPatch) -> None:
    """Шаг-функция переживает повторный запуск: ADD COLUMN не умеет IF NOT EXISTS."""
    with db.conn() as c:
        c.execute("ALTER TABLE usage DROP COLUMN reserved_at")
        c.execute("ALTER TABLE usage DROP COLUMN reserved_src")
        assert "reserved_at" not in _columns("usage")
        db._add_reserve_columns(c)
        assert {"reserved_at", "reserved_src"} <= _columns("usage")
        db._add_reserve_columns(c)  # второй прогон не должен падать

    assert {"reserved_at", "reserved_src"} <= _columns("usage")
