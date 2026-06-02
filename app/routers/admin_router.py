from fastapi import APIRouter, Depends, HTTPException, Body
from motor.motor_asyncio import AsyncIOMotorDatabase
from app.auth import get_admin_user
from app.database import get_db
from app.models.schemas import AdminModelUpdate
from app.services.user_service import serialize_doc
from datetime import datetime, timezone
import httpx
from app.config import settings

router = APIRouter()

@router.get("/users")
async def list_users(
    admin_user: dict = Depends(get_admin_user),
    db: AsyncIOMotorDatabase = Depends(get_db),
):
    """List all users (Admin only)"""
    cursor = db.users.find().sort("createdAt", -1)
    users = await cursor.to_list(length=1000)
    return [serialize_doc(user) for user in users]

@router.put("/users/{uid}")
async def edit_user(
    uid: str,
    update_data: dict = Body(...),
    admin_user: dict = Depends(get_admin_user),
    db: AsyncIOMotorDatabase = Depends(get_db),
):
    """Edit user details (Admin only)"""
    allowed_fields = {"name", "upiId", "role"}
    filtered_data = {k: v for k, v in update_data.items() if k in allowed_fields}
    if not filtered_data:
        raise HTTPException(status_code=400, detail="No valid fields to update")
        
    filtered_data["updatedAt"] = datetime.now(timezone.utc)
    result = await db.users.update_one({"firebaseUid": uid}, {"$set": filtered_data})
    
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="User not found")
        
    updated = await db.users.find_one({"firebaseUid": uid})
    return serialize_doc(updated)

@router.delete("/users/{uid}")
async def delete_user(
    uid: str,
    admin_user: dict = Depends(get_admin_user),
    db: AsyncIOMotorDatabase = Depends(get_db),
):
    """Delete a user (Admin only)"""
    result = await db.users.delete_one({"firebaseUid": uid})
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="User not found")
    return {"message": "User deleted"}

@router.post("/users/{uid}/reset-password")
async def reset_password(
    uid: str,
    admin_user: dict = Depends(get_admin_user),
    db: AsyncIOMotorDatabase = Depends(get_db),
):
    """Trigger password reset email (Admin only)"""
    user = await db.users.find_one({"firebaseUid": uid})
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
        
    email = user.get("email")
    if not email:
        raise HTTPException(status_code=400, detail="User has no email")
        
    try:
        # Use Supabase REST API to send reset email
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{settings.SUPABASE_URL.rstrip('/')}/auth/v1/recover",
                headers={"apikey": settings.SUPABASE_KEY},
                json={"email": email}
            )
            response.raise_for_status()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to send reset email: {str(e)}")
        
    return {"message": f"Password reset email sent to {email}"}

@router.get("/prompts")
async def list_prompts(
    admin_user: dict = Depends(get_admin_user),
    db: AsyncIOMotorDatabase = Depends(get_db),
):
    """List all LLM prompt history (Admin only)"""
    cursor = db.llm_history.find().sort("createdAt", -1).limit(200)
    history = await cursor.to_list(length=200)
    return [serialize_doc(h) for h in history]

@router.get("/issues")
async def list_issues(
    admin_user: dict = Depends(get_admin_user),
    db: AsyncIOMotorDatabase = Depends(get_db),
):
    """List all user issues (Admin only)"""
    cursor = db.issues.find().sort("createdAt", -1)
    issues = await cursor.to_list(length=1000)
    return [serialize_doc(issue) for issue in issues]

@router.put("/issues/{issue_id}")
async def update_issue_status(
    issue_id: str,
    update_data: dict = Body(...),
    admin_user: dict = Depends(get_admin_user),
    db: AsyncIOMotorDatabase = Depends(get_db),
):
    """Update issue status (Admin only)"""
    new_status = update_data.get("status")
    if not new_status:
        raise HTTPException(status_code=400, detail="Missing status")
        
    from bson import ObjectId
    result = await db.issues.update_one(
        {"_id": ObjectId(issue_id)}, 
        {"$set": {"status": new_status, "updatedAt": datetime.now(timezone.utc)}}
    )
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="Issue not found")
    
    updated = await db.issues.find_one({"_id": ObjectId(issue_id)})
    return serialize_doc(updated)

@router.get("/models")
async def get_models(
    admin_user: dict = Depends(get_admin_user),
    db: AsyncIOMotorDatabase = Depends(get_db),
):
    """Get active model configuration and available models with stats (Admin only)"""
    # Get active model
    config = await db.settings.find_one({"_id": "llm_settings"})
    active_model = config.get("activeModel", "gemini-1.5-flash") if config else "gemini-1.5-flash"
    
    # Available models list
    available_models = [
        {"id": "gemini-1.5-flash", "name": "Gemini 1.5 Flash", "provider": "Google"},
        {"id": "gemini-1.5-pro", "name": "Gemini 1.5 Pro", "provider": "Google"},
        {"id": "gemini-1.0-pro", "name": "Gemini 1.0 Pro", "provider": "Google"},
        {"id": "gemma:2b", "name": "Gemma 2B", "provider": "Ollama (Local)"},
        {"id": "llama3", "name": "Llama 3", "provider": "Ollama (Local)"}
    ]
    
    # Fetch stats for models
    stats_cursor = db.llm_stats.find({})
    stats_list = await stats_cursor.to_list(length=100)
    stats_map = {s["model"]: s for s in stats_list}
    
    for model in available_models:
        model_stats = stats_map.get(model["id"], {})
        model["totalRequests"] = model_stats.get("totalRequests", 0)
        model["totalTokens"] = model_stats.get("totalTokens", 0)
        model["isActive"] = (model["id"] == active_model)
        
    return {
        "activeModel": active_model,
        "availableModels": available_models
    }

@router.put("/models/active")
async def set_active_model(
    data: AdminModelUpdate,
    admin_user: dict = Depends(get_admin_user),
    db: AsyncIOMotorDatabase = Depends(get_db),
):
    """Set active LLM model (Admin only)"""
    await db.settings.update_one(
        {"_id": "llm_settings"},
        {"$set": {"activeModel": data.activeModel, "updatedAt": datetime.now(timezone.utc)}},
        upsert=True
    )
    return {"message": f"Active model set to {data.activeModel}"}
