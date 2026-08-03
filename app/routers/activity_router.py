from fastapi import APIRouter, Depends, HTTPException
from motor.motor_asyncio import AsyncIOMotorDatabase

from app.auth import get_current_user
from app.database import get_db
from app.services import audit_service, user_service

router = APIRouter()


@router.get("/activity")
async def get_activity(
    limit: int = 50,
    skip: int = 0,
    current_user: dict = Depends(get_current_user),
    db: AsyncIOMotorDatabase = Depends(get_db),
):
    """Everything that touched the caller's expenses, groups, and settlements."""
    user_id = await user_service.get_user_internal_id(db, current_user["uid"])
    if not user_id:
        raise HTTPException(status_code=404, detail="User not found")
    return await audit_service.get_activity(db, user_id, limit=limit, skip=skip)


@router.post("/activity/{log_id}/undo")
async def undo_activity(
    log_id: str,
    current_user: dict = Depends(get_current_user),
    db: AsyncIOMotorDatabase = Depends(get_db),
):
    """Reverse a logged action — restore what it changed or remove what it made."""
    user_id = await user_service.get_user_internal_id(db, current_user["uid"])
    if not user_id:
        raise HTTPException(status_code=404, detail="User not found")
    return await audit_service.undo(db, user_id, log_id)
