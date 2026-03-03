from fastapi import APIRouter, Depends, HTTPException, Request
from motor.motor_asyncio import AsyncIOMotorDatabase
from app.auth import get_current_user
from app.database import get_db
from app.services import event_service, expense_service, user_service

router = APIRouter()


@router.get("/user/events")
async def get_user_events(
    current_user: dict = Depends(get_current_user),
    db: AsyncIOMotorDatabase = Depends(get_db),
):
    """List all events the current user is a member of."""
    user_id = await user_service.get_user_internal_id(db, current_user["uid"])
    if not user_id:
        raise HTTPException(status_code=404, detail="User not found")

    try:
        result = await event_service.get_user_events(db, user_id)
        return result
    except Exception as e:
        print(f"Error in getUserEvents route: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/event/{event_id}")
async def get_event(
    event_id: str,
    current_user: dict = Depends(get_current_user),
    db: AsyncIOMotorDatabase = Depends(get_db),
):
    """Get single event details."""
    event = await event_service.get_event_by_id(db, event_id)
    if not event:
        raise HTTPException(status_code=404, detail="Event not found")
    return event


@router.post("/event")
async def create_event(
    request: Request,
    current_user: dict = Depends(get_current_user),
    db: AsyncIOMotorDatabase = Depends(get_db),
):
    """Create a new event."""
    user_id = await user_service.get_user_internal_id(db, current_user["uid"])
    if not user_id:
        raise HTTPException(status_code=404, detail="User not found")

    request_data = await request.json()
    if "id" in request_data:
        del request_data["id"]

    try:
        result = await event_service.save_event(db, user_id, request_data)
        return result
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.put("/event")
async def update_event(
    request: Request,
    current_user: dict = Depends(get_current_user),
    db: AsyncIOMotorDatabase = Depends(get_db),
):
    """Update an existing event."""
    user_id = await user_service.get_user_internal_id(db, current_user["uid"])
    if not user_id:
        raise HTTPException(status_code=404, detail="User not found")

    request_data = await request.json()
    if "id" not in request_data:
        raise HTTPException(status_code=400, detail="Event ID required")

    try:
        result = await event_service.save_event(db, user_id, request_data)
        return result
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.delete("/event/{event_id}")
async def delete_event(
    event_id: str,
    current_user: dict = Depends(get_current_user),
    db: AsyncIOMotorDatabase = Depends(get_db),
):
    """Delete an event and its associated expenses."""
    result = await event_service.delete_event(db, event_id)
    return result


@router.get("/event/{event_id}/expenses")
async def get_event_expenses(
    event_id: str,
    current_user: dict = Depends(get_current_user),
    db: AsyncIOMotorDatabase = Depends(get_db),
):
    """List all expenses for an event."""
    user_id = await user_service.get_user_internal_id(db, current_user["uid"])
    if not user_id:
        raise HTTPException(status_code=404, detail="User not found")

    result = await expense_service.get_event_expenses_with_summary(
        db, user_id, event_id
    )
    return result


@router.get("/user/event/{event_id}/dues")
async def get_event_dues_for_user(
    event_id: str,
    current_user: dict = Depends(get_current_user),
    db: AsyncIOMotorDatabase = Depends(get_db),
):
    """Calculate who owes whom within an event for the current user."""
    user_id = await user_service.get_user_internal_id(db, current_user["uid"])
    if not user_id:
        raise HTTPException(status_code=404, detail="User not found")

    result = await event_service.get_event_dues_for_user(db, event_id, user_id)
    return result


@router.get("/event/{event_id}/dues")
async def get_event_dues(
    event_id: str,
    current_user: dict = Depends(get_current_user),
    db: AsyncIOMotorDatabase = Depends(get_db),
):
    """Calculate all dues within an event."""
    result = await event_service.get_event_dues(db, event_id)
    return result
