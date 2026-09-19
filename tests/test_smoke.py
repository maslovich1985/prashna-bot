"""Каркас работает: БД создаётся в tmp_path, схема применена."""

from __future__ import annotations

from pathlib import Path

from app import db


def test_db_isolated(db_path: Path) -> None:
    assert db_path.exists()
    db.upsert_user(1, "tester")
    assert db.get_user(1)["username"] == "tester"


def test_db_is_empty_for_each_test() -> None:
    assert db.get_user(1) is None
