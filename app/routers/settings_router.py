"""Public site settings -- unauthenticated, read-only.

Exposes only the subset that the client needs before the user is logged in
(e.g. the signup page checks whether registrations are open).
"""

from fastapi import APIRouter, Depends
from motor.motor_asyncio import AsyncIOMotorDatabase

from app.database import get_db
from app.services.site_settings import get_site_settings

router = APIRouter()


@router.get("/settings/public")
async def public_settings(
    db: AsyncIOMotorDatabase = Depends(get_db),
):
    """Return the public slice of site settings (no auth required)."""
    ss = await get_site_settings(db)

    # Count real users (not phantoms, not deleted tombstones).
    user_count = await db.users.count_documents(
        {"isPhantom": {"$ne": True}, "isDeleted": {"$ne": True}}
    )

    max_limit = ss.get("maxSignupLimit", 0)
    signups_enabled = ss.get("signupsEnabled", True)

    # Auto-disable when the cap is reached.
    if max_limit > 0 and user_count >= max_limit:
        signups_enabled = False

    return {
        "signupsEnabled": signups_enabled,
        "llmEnabled": ss.get("llmEnabled", False),
        "maxSignupLimit": max_limit,
        "currentUserCount": user_count,
    }
