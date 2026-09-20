"""Схема квот и платежей (B-01): таблицы есть, charge_id не даёт выдать доступ дважды."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from app import db

PAID_AT = "2026-09-21T10:00:00+00:00"


def _columns(table: str) -> set[str]:
    with db.conn() as c:
        return {r["name"] for r in c.execute(f"PRAGMA table_info({table})")}


def _insert_payment(charge_id: str, user_id: int = 1) -> None:
    with db.conn() as c:
        c.execute(
            "INSERT INTO payments (charge_id, user_id, plan, stars, paid_at) VALUES (?,?,?,?,?)",
            (charge_id, user_id, "month", 250, PAID_AT),
        )


def test_entitlements_columns() -> None:
    assert _columns("entitlements") == {
        "user_id",
        "plan",
        "expires_at",
        "questions_left",
        "trial_used",
        "updated_at",
    }


def test_payments_columns() -> None:
    assert _columns("payments") == {
        "charge_id",
        "user_id",
        "plan",
        "stars",
        "paid_at",
        "refunded_at",
    }


def test_entitlements_defaults() -> None:
    with db.conn() as c:
        c.execute("INSERT INTO entitlements (user_id, updated_at) VALUES (1, ?)", (PAID_AT,))
        row = c.execute("SELECT * FROM entitlements WHERE user_id=1").fetchone()
    # Пакеты и пробные считаются от нуля, а не от NULL: иначе арифметика квот
    # в B-03 начнётся с приведения типов.
    assert (row["questions_left"], row["trial_used"]) == (0, 0)
    assert (row["plan"], row["expires_at"]) == (None, None)


def test_duplicate_charge_id_rejected() -> None:
    _insert_payment("ch_1")
    # Повторный successful_payment с тем же id не должен начислить доступ дважды
    with pytest.raises(sqlite3.IntegrityError):
        _insert_payment("ch_1", user_id=2)


def test_payment_refunded_at_is_optional() -> None:
    _insert_payment("ch_2")
    with db.conn() as c:
        assert c.execute("SELECT refunded_at FROM payments").fetchone()["refunded_at"] is None


def test_tables_appear_on_old_db(monkeypatch: pytest.MonkeyPatch) -> None:
    """База, созданная до B-01, догоняется шагом миграции, а не только SCHEMA."""
    with db.conn() as c:
        c.executescript("DROP TABLE entitlements; DROP TABLE payments")
        c.execute("PRAGMA user_version = 0")
    monkeypatch.setattr(db, "_backup_before_migrate", lambda: None)
    monkeypatch.setattr(db, "SCHEMA", "")  # SCHEMA не должна подменять собой миграцию

    db.migrate()

    with db.conn() as c:
        tables = {r["name"] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"entitlements", "payments"} <= tables


def test_migration_keeps_existing_data(monkeypatch: pytest.MonkeyPatch, db_path: Path) -> None:
    db.upsert_user(1, "tester")
    pid = db.save_prashna(1, "Вопрос?", 10, "Томск", "КАРТА", "ОТВЕТ")
    with db.conn() as c:
        c.executescript("DROP TABLE entitlements; DROP TABLE payments")
        c.execute("PRAGMA user_version = 0")
    monkeypatch.setattr(db, "_backup_before_migrate", lambda: None)

    db.init()

    assert db.get_user(1)["username"] == "tester"
    assert db.get_prashna(1, pid)["answer"] == "ОТВЕТ"
    with db.conn() as c:
        assert int(c.execute("PRAGMA user_version").fetchone()[0]) == len(db.MIGRATIONS)
