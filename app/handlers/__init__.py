"""Сборка роутеров. Порядок подключения = порядок проверки хэндлеров в aiogram.

`prashna` обязан идти последним: его catch-all `F.text` и пустой `@router.message()`
перехватят всё, что не разобрали роутеры выше, включая ввод города в FSM.
"""

from __future__ import annotations

from aiogram import Dispatcher

from . import basic, billing, place, prashna, privacy

ROUTERS = (basic.router, billing.router, privacy.router, place.router, prashna.router)


def register(dp: Dispatcher) -> None:
    for router in ROUTERS:
        dp.include_router(router)
