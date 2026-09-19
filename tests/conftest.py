"""Общие фикстуры. БД для каждого теста — отдельный файл в tmp_path."""

from __future__ import annotations

import dataclasses
from collections.abc import Iterator
from pathlib import Path

import pytest

from app import db as db_module
from app.config import settings


@pytest.fixture(autouse=True)
def db_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    # app/config.py читает env на импорте, поэтому monkeypatch.setenv("DB_PATH") уже
    # ни на что не влияет: подменяем сам объект настроек в модуле, который его использует.
    path = tmp_path / "test.sqlite3"
    monkeypatch.setattr(db_module, "settings", dataclasses.replace(settings, db_path=path))
    db_module.init()
    yield path
