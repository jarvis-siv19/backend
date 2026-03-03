from fastapi import APIRouter, Depends, HTTPException, Request
from motor.motor_asyncio import AsyncIOMotorDatabase
from app.auth import get_current_user
from app.database import get_db
from app.services import expense_service, user_service
from app.models.schemas import PersonalExpensesRequest

router = APIRouter()


@router.post("/expense")
async def create_expense(
    request: Request,
    current_user: dict = Depends(get_current_user),
    db: AsyncIOMotorDatabase = Depends(get_db),
):
    """Create a new expense (can be event, friend, or personal)."""
    user_id = await user_service.get_user_internal_id(db, current_user["uid"])
    if not user_id:
        raise HTTPException(status_code=404, detail="User not found")

    request_data = await request.json()
    try:
        result = await expense_service.create_expense(db, user_id, request_data)
        return result
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/expense/{expense_id}")
async def get_expense(
    expense_id: str,
    current_user: dict = Depends(get_current_user),
    db: AsyncIOMotorDatabase = Depends(get_db),
):
    """Get full expense details."""
    user_id = await user_service.get_user_internal_id(db, current_user["uid"])
    if not user_id:
        raise HTTPException(status_code=404, detail="User not found")

    result = await expense_service.get_expense_by_id(db, expense_id, user_id)
    if not result:
        raise HTTPException(status_code=404, detail="Expense not found")
    return result


@router.put("/expense/{expense_id}")
async def update_expense(
    expense_id: str,
    request: Request,
    current_user: dict = Depends(get_current_user),
    db: AsyncIOMotorDatabase = Depends(get_db),
):
    """Update an expense."""
    user_id = await user_service.get_user_internal_id(db, current_user["uid"])
    if not user_id:
        raise HTTPException(status_code=404, detail="User not found")

    request_data = await request.json()
    result = await expense_service.update_expense(db, user_id, expense_id, request_data)
    return result


@router.delete("/expense/{expense_id}")
async def delete_expense(
    expense_id: str,
    current_user: dict = Depends(get_current_user),
    db: AsyncIOMotorDatabase = Depends(get_db),
):
    """Delete an expense."""
    result = await expense_service.delete_expense(db, expense_id)
    return result


@router.post("/expenses/personal")
async def get_personal_expenses(
    request: Request,
    current_user: dict = Depends(get_current_user),
    db: AsyncIOMotorDatabase = Depends(get_db),
):
    """Get all personal expenses for the current user."""
    user_id = await user_service.get_user_internal_id(db, current_user["uid"])
    if not user_id:
        raise HTTPException(status_code=404, detail="User not found")

    request_data = await request.json()
    result = await expense_service.get_all_expenses_for_user(db, user_id, request_data)
    return result
