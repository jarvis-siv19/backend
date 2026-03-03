from fastapi import APIRouter, Depends, HTTPException, Request
from motor.motor_asyncio import AsyncIOMotorDatabase
from app.auth import get_current_user
from app.database import get_db
from app.services import friend_service, user_service
from app.models.schemas import FriendAddRequest, FriendDeleteRequest

router = APIRouter()


@router.get("/user/friends")
async def get_user_friends(
    current_user: dict = Depends(get_current_user),
    db: AsyncIOMotorDatabase = Depends(get_db),
):
    """List all friends for the current user with balance info."""
    user_id = await user_service.get_user_internal_id(db, current_user["uid"])
    if not user_id:
        raise HTTPException(status_code=404, detail="User not found")

    result = await friend_service.get_friend_list(db, user_id)
    return result


@router.post("/addFriend")
async def add_friend(
    request: FriendAddRequest,
    current_user: dict = Depends(get_current_user),
    db: AsyncIOMotorDatabase = Depends(get_db),
):
    """Add a friend by their friend code."""
    user_id = await user_service.get_user_internal_id(db, current_user["uid"])
    if not user_id:
        raise HTTPException(status_code=404, detail="User not found")

    result = await friend_service.add_friend(db, user_id, request.model_dump())
    return result


@router.delete("/deleteFriend")
async def delete_friend(
    request: FriendDeleteRequest,
    current_user: dict = Depends(get_current_user),
    db: AsyncIOMotorDatabase = Depends(get_db),
):
    """Remove a friend."""
    user_id = await user_service.get_user_internal_id(db, current_user["uid"])
    if not user_id:
        raise HTTPException(status_code=404, detail="User not found")

    result = await friend_service.delete_friend(db, user_id, request.model_dump())
    return result


@router.get("/user/expense/friend/{friend_id}")
async def get_friend_expenses(
    friend_id: str,
    current_user: dict = Depends(get_current_user),
    db: AsyncIOMotorDatabase = Depends(get_db),
):
    """Get detailed friend info with shared expenses and balance."""
    user_id = await user_service.get_user_internal_id(db, current_user["uid"])
    if not user_id:
        raise HTTPException(status_code=404, detail="User not found")

    result = await friend_service.get_friend_details(db, user_id, friend_id)
    if not result:
        raise HTTPException(status_code=400, detail="Friend not found or not a friend")
    return result


@router.post("/user/expense/friend/{friend_id}/settleup")
async def settle_up_friend(
    friend_id: str,
    current_user: dict = Depends(get_current_user),
    db: AsyncIOMotorDatabase = Depends(get_db),
):
    """Mark all expenses with this friend as settled."""
    user_id = await user_service.get_user_internal_id(db, current_user["uid"])
    if not user_id:
        raise HTTPException(status_code=404, detail="User not found")

    result = await friend_service.settle_up_friend_dues(db, user_id, friend_id)
    return result
