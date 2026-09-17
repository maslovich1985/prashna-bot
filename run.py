#!/usr/bin/env python3
"""Точка входа: python run.py"""
import asyncio

from app.bot import run
from app.config import validate

if __name__ == "__main__":
    validate()
    try:
        asyncio.run(run())
    except (KeyboardInterrupt, SystemExit):
        pass
