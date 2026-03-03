from fastapi import APIRouter, Depends, HTTPException, Request
from motor.motor_asyncio import AsyncIOMotorDatabase
from app.auth import get_current_user
from app.database import get_db
from app.services import payment_service, user_service

router = APIRouter()


@router.post("/payment")
async def create_payment(
    request: Request,
    current_user: dict = Depends(get_current_user),
    db: AsyncIOMotorDatabase = Depends(get_db),
):
    """Create a payment page."""
    user_id = await user_service.get_user_internal_id(db, current_user["uid"])
    if not user_id:
        raise HTTPException(status_code=404, detail="User not found")

    request_data = await request.json()
    page = await payment_service.create_page(db, user_id, request_data)
    return page


@router.get("/payment")
async def view_payment_pages(
    current_user: dict = Depends(get_current_user),
    db: AsyncIOMotorDatabase = Depends(get_db),
):
    """View all payment pages for the current user."""
    user_id = await user_service.get_user_internal_id(db, current_user["uid"])
    if not user_id:
        raise HTTPException(status_code=404, detail="User not found")

    pages = await payment_service.view_pages(db, user_id)
    return pages


@router.get("/payment/{link:path}")
async def view_payment_page(
    link: str,
    db: AsyncIOMotorDatabase = Depends(get_db),
):
    """Public payment page - no authentication required."""
    page = await payment_service.view_page(db, link)
    if not page:
        raise HTTPException(status_code=404, detail="Payment page not found")
    return page
