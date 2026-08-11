"""DB-backed site settings with in-memory caching.

The admin dashboard can toggle features at runtime without a redeploy.
Settings fall back to the .env values when no DB override exists.
The Mongo document lives in the existing ``app_config`` collection under
``_id="site_settings"``.
"""

import time
from datetime import datetime, timezone

from fastapi import HTTPException
from motor.motor_asyncio import AsyncIOMotorDatabase

from app.config import settings

_CACHE_TTL_SECONDS = 60
_cache: dict | None = None
_cache_ts: float = 0.0

# Fields the admin PUT can write.
ALLOWED_FIELDS = {
    "llmEnabled",
    "signupsEnabled",
    "maxSignupLimit",
    "emailEnabled",
    "emailProvider",
    "defaultCurrency",
    "appBaseUrl",
}


def _env_defaults() -> dict:
    """Build the complete defaults dict from .env values."""
    return {
        "llmEnabled": settings.LLM_ENABLED,
        "signupsEnabled": True,
        "maxSignupLimit": 0,  # 0 = unlimited
        "emailEnabled": settings.ZEPTOMAIL_ENABLED,
        "emailProvider": "zeptomail",
        "defaultCurrency": settings.DEFAULT_CURRENCY,
        "appBaseUrl": settings.APP_BASE_URL,
    }


async def get_site_settings(db: AsyncIOMotorDatabase) -> dict:
    """Return the merged site settings (DB overrides on top of .env defaults).

    Results are cached in memory for 60 s to avoid a DB round-trip on every
    request.
    """
    global _cache, _cache_ts

    now = time.monotonic()
    if _cache is not None and (now - _cache_ts) < _CACHE_TTL_SECONDS:
        return dict(_cache)

    merged = _env_defaults()
    doc = await db.app_config.find_one({"_id": "site_settings"})
    if doc:
        for key in ALLOWED_FIELDS:
            if key in doc and doc[key] is not None:
                merged[key] = doc[key]

    _cache = merged
    _cache_ts = now
    return dict(merged)


def get_cached_settings() -> dict:
    """Return the last cached settings, or .env defaults if not yet loaded.

    Safe to call from sync helpers (e.g. email_service._enabled) -- never
    touches the database.
    """
    if _cache is not None:
        return dict(_cache)
    return _env_defaults()


def invalidate_cache() -> None:
    """Force the next get_site_settings() call to re-read from the DB."""
    global _cache, _cache_ts
    _cache = None
    _cache_ts = 0.0


async def update_site_settings(db: AsyncIOMotorDatabase, data: dict) -> dict:
    """Validate, persist and return the new merged settings."""
    filtered = {k: v for k, v in data.items() if k in ALLOWED_FIELDS}
    if not filtered:
        raise HTTPException(status_code=400, detail="No valid fields to update")

    # -- type guards -------------------------------------------------------
    for field in ("llmEnabled", "signupsEnabled", "emailEnabled"):
        if field in filtered and not isinstance(filtered[field], bool):
            raise HTTPException(
                status_code=400, detail=f"{field} must be a boolean"
            )

    if "maxSignupLimit" in filtered:
        val = filtered["maxSignupLimit"]
        if not isinstance(val, int) or val < 0:
            raise HTTPException(
                status_code=400,
                detail="maxSignupLimit must be a non-negative integer",
            )

    if "emailProvider" in filtered:
        provider = filtered["emailProvider"].strip().lower()
        if provider not in ("zeptomail", "zohomail"):
            raise HTTPException(status_code=400, detail="Invalid emailProvider")
        filtered["emailProvider"] = provider

    if "defaultCurrency" in filtered:
        from app.services import fx_service
        currency = filtered["defaultCurrency"].strip().upper()
        if currency not in fx_service.SUPPORTED_CURRENCIES:
            raise HTTPException(status_code=400, detail="Unsupported currency")
        filtered["defaultCurrency"] = currency

    filtered["updatedAt"] = datetime.now(timezone.utc)
    await db.app_config.update_one(
        {"_id": "site_settings"}, {"$set": filtered}, upsert=True
    )

    invalidate_cache()
    return await get_site_settings(db)
