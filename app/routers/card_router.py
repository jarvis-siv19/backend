from fastapi import APIRouter, Depends, HTTPException
from motor.motor_asyncio import AsyncIOMotorDatabase
from pydantic import BaseModel
from typing import Optional

from app.auth import get_current_user
from app.database import get_db
from app.services import card_service, user_service

router = APIRouter()


class CardRequest(BaseModel):
    name: str
    # Day of the month the bill falls due; the bill recurs, so this is not a date.
    dueDay: int
    last4: str = ""
    amount: Optional[float] = None
    currency: Optional[str] = None
    remindersEnabled: bool = True


async def _require_user(db: AsyncIOMotorDatabase, current_user: dict) -> str:
    user_id = await user_service.get_user_internal_id(db, current_user["uid"])
    if not user_id:
        raise HTTPException(status_code=404, detail="User not found")
    return user_id


@router.get("/cards")
async def list_cards(
    current_user: dict = Depends(get_current_user),
    db: AsyncIOMotorDatabase = Depends(get_db),
):
    """The caller's credit cards and when each bill is next due."""
    user_id = await _require_user(db, current_user)
    return await card_service.list_cards(db, user_id)


@router.post("/cards")
async def create_card(
    request: CardRequest,
    current_user: dict = Depends(get_current_user),
    db: AsyncIOMotorDatabase = Depends(get_db),
):
    """Track a card's bill date and get reminders 10 and 5 days ahead."""
    user_id = await _require_user(db, current_user)
    try:
        return await card_service.create_card(db, user_id, request.model_dump())
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.put("/cards/{card_id}")
async def update_card(
    card_id: str,
    request: CardRequest,
    current_user: dict = Depends(get_current_user),
    db: AsyncIOMotorDatabase = Depends(get_db),
):
    """Update a card."""
    user_id = await _require_user(db, current_user)
    try:
        card = await card_service.update_card(
            db, user_id, card_id, request.model_dump()
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if not card:
        raise HTTPException(status_code=404, detail="Card not found")
    return card


@router.delete("/cards/{card_id}")
async def delete_card(
    card_id: str,
    current_user: dict = Depends(get_current_user),
    db: AsyncIOMotorDatabase = Depends(get_db),
):
    """Stop tracking a card."""
    user_id = await _require_user(db, current_user)
    if not await card_service.delete_card(db, user_id, card_id):
        raise HTTPException(status_code=404, detail="Card not found")
    return {"success": "true", "message": "Card removed"}
