"""Геокодирование города и определение часового пояса."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

import httpx
from timezonefinder import TimezoneFinder

from . import db
from .config import settings

log = logging.getLogger(__name__)
_tf = TimezoneFinder()
_lock = asyncio.Lock()
_last_call = 0.0


@dataclass
class Place:
    name: str
    lat: float
    lon: float
    tz: str


def tz_for(lat: float, lon: float) -> str:
    return _tf.timezone_at(lat=lat, lng=lon) or "UTC"


async def geocode(query: str) -> Place | None:
    """Ищет город через Nominatim. Результат кэшируется в SQLite."""
    q = query.strip()
    if not q:
        return None

    cached = db.geocache_get(q)
    if cached:
        return Place(cached["place"], cached["lat"], cached["lon"], cached["tz"])

    global _last_call
    async with _lock:  # Nominatim: не более 1 запроса в секунду
        loop = asyncio.get_running_loop()
        wait = 1.1 - (loop.time() - _last_call)
        if wait > 0:
            await asyncio.sleep(wait)
        _last_call = loop.time()

        try:
            async with httpx.AsyncClient(timeout=15) as client:
                r = await client.get(
                    settings.geocoder_url,
                    params={"q": q, "format": "json", "limit": 1, "accept-language": "ru"},
                    headers={"User-Agent": settings.geocoder_user_agent},
                )
                r.raise_for_status()
                data = r.json()
        except Exception as e:
            log.warning("Геокодер недоступен: %s", e)
            return None

    if not data:
        return None

    item = data[0]
    lat, lon = float(item["lat"]), float(item["lon"])
    name = item.get("display_name", q).split(",")[0].strip() or q
    place = Place(name, lat, lon, tz_for(lat, lon))
    db.geocache_put(q, place.name, place.lat, place.lon, place.tz)
    return place


def place_from_coords(lat: float, lon: float, name: str | None = None) -> Place:
    return Place(name or f"{lat:.3f}, {lon:.3f}", lat, lon, tz_for(lat, lon))


def default_place() -> Place:
    return Place(
        settings.default_city, settings.default_lat, settings.default_lon, settings.default_tz
    )
