from bson import ObjectId
from fastapi import APIRouter, Depends, HTTPException
from motor.motor_asyncio import AsyncIOMotorDatabase
from app.auth import get_current_user
from app.database import get_db
from app.services import event_service, user_service

router = APIRouter()


@router.get("/{context_type}/{context_id}/users")
async def get_context_users(
    context_type: str,
    context_id: str,
    current_user: dict = Depends(get_current_user),
    db: AsyncIOMotorDatabase = Depends(get_db),
):
    """Get users related to a context (event or friend) for expense 'paid by' dropdown."""
    user_id = await user_service.get_user_internal_id(db, current_user["uid"])
    if not user_id:
        raise HTTPException(status_code=404, detail="User not found")

    if context_type not in ("event", "friend"):
        raise HTTPException(status_code=400, detail="Invalid context type. Use 'event' or 'friend'.")

    # Only expose members of an event you belong to / people you're friends with.
    if context_type == "event":
        if not await event_service.is_event_member(db, context_id, user_id):
            raise HTTPException(status_code=404, detail="Event not found")
    else:
        try:
            me = await db.users.find_one({"_id": ObjectId(user_id)}, {"friends": 1})
        except Exception:
            me = None
        try:
            friend_oid = ObjectId(context_id)
        except Exception:
            friend_oid = None
        if not me or friend_oid is None or friend_oid not in me.get("friends", []):
            raise HTTPException(status_code=404, detail="Friend not found")

    result = await event_service.get_event_or_friend_users(db, user_id, context_type, context_id)
    return result
