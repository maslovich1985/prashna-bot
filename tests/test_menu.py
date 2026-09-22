"""Синее меню команд и отсутствие UI-литералов в хэндлерах (F-01)."""

from __future__ import annotations

import ast
from pathlib import Path

from app import bot as bot_module
from app.handlers import ROUTERS

HANDLERS_DIR = Path(bot_module.__file__).parent / "handlers"

# Команды, которых в меню быть не должно, и почему.
HIDDEN = {
    "refund": "админская",
    "stats": "админская",
    "subscribe": "продажи закрыты, пока не проставлены цены (§15.1)",
}


def _menu() -> set[str]:
    return {c.command for c in bot_module.BOT_COMMANDS}


def _handler_commands() -> set[str]:
    found: set[str] = set()
    for path in HANDLERS_DIR.glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and getattr(node.func, "id", None) == "Command":
                found.update(a.value for a in node.args if isinstance(a, ast.Constant))
    return found


def test_every_menu_command_has_a_handler() -> None:
    assert _menu() <= _handler_commands()


def test_every_public_command_is_in_the_menu() -> None:
    # Команда без строки в меню существует только для тех, кто про неё знает.
    assert _handler_commands() - HIDDEN.keys() == _menu()


def test_menu_descriptions_are_filled() -> None:
    assert all(c.description.strip() for c in bot_module.BOT_COMMANDS)


def test_handlers_hold_no_user_facing_literals() -> None:
    """Тексты живут в app/texts.py: править формулировки надо в одном месте.

    Ловим кириллицу в строках вне докстрингов и вне вызовов логгера — именно так
    UI-литерал и просачивается обратно в хэндлер.
    """
    offenders: list[str] = []
    for path in HANDLERS_DIR.glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        # clean=False обязателен: get_docstring по умолчанию чистит отступы,
        # и строка перестаёт совпадать с константой из дерева.
        docstrings = {
            ast.get_docstring(n, clean=False)
            for n in ast.walk(tree)
            if isinstance(n, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef)
        }
        logged: set[int] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                if getattr(node.func.value, "id", None) == "log":
                    logged.update(id(a) for a in ast.walk(node))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Constant) or not isinstance(node.value, str):
                continue
            if node.value in docstrings or id(node) in logged:
                continue
            if any("а" <= ch.lower() <= "я" for ch in node.value):
                offenders.append(f"{path.name}:{node.lineno}: {node.value[:40]}")
    assert offenders == []


def test_routers_are_all_named() -> None:
    assert all(r.name for r in ROUTERS)
