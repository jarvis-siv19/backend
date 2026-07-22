"""App version / update-availability endpoint.

The mobile clients call GET /version on launch to decide whether to show an
"update available" banner (optional) or a blocking "update required" dialog
(when the running build is older than the minimum supported version).

The source of truth is a single Mongo doc (``app_config`` collection,
``_id="app_version"``) so the version can be bumped at runtime without a
redeploy; it falls back to the APP_* settings when that doc is absent. Admins
set it via PUT /db/admin/app-version.
"""

from fastapi import APIRouter, Depends
from motor.motor_asyncio import AsyncIOMotorDatabase

from app.config import settings
from app.database import get_db

router = APIRouter()

_ALLOWED_FIELDS = {
    "latest",
    "latestBuild",
    "minSupported",
    "android",
    "ios",
    "notes",
}


def _defaults() -> dict:
    return {
        "latest": settings.APP_LATEST_VERSION,
        "latestBuild": settings.APP_LATEST_BUILD,
        "minSupported": settings.APP_MIN_SUPPORTED_VERSION,
        "android": {"url": settings.APP_ANDROID_URL},
        "ios": {"url": settings.APP_IOS_URL},
        "notes": settings.APP_UPDATE_NOTES,
    }


async def get_version_info(db: AsyncIOMotorDatabase) -> dict:
    """Merge the runtime override doc over the settings defaults."""
    info = _defaults()
    doc = await db.app_config.find_one({"_id": "app_version"})
    if doc:
        for key in _ALLOWED_FIELDS:
            if key in doc and doc[key] is not None:
                info[key] = doc[key]
    return info


@router.get("/version", tags=["Version"])
async def get_version(db: AsyncIOMotorDatabase = Depends(get_db)):
    """Public: latest version + minimum supported version + download URLs."""
    return await get_version_info(db)
