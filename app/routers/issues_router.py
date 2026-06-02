from fastapi import APIRouter, Depends
from motor.motor_asyncio import AsyncIOMotorDatabase
from app.auth import get_current_user
from app.database import get_db
from app.models.schemas import IssueCreate, IssueResponse
from app.services.user_service import serialize_doc
from datetime import datetime, timezone

router = APIRouter()

@router.post("/", response_model=IssueResponse)
async def create_issue(
    request: IssueCreate,
    current_user: dict = Depends(get_current_user),
    db: AsyncIOMotorDatabase = Depends(get_db),
):
    """Submit a new issue or feature request."""
    issue_doc = {
        "userId": current_user["uid"],
        "type": request.type,
        "title": request.title,
        "description": request.description,
        "status": "open",
        "createdAt": datetime.now(timezone.utc)
    }
    
    result = await db.issues.insert_one(issue_doc)
    issue_doc["_id"] = result.inserted_id
    
    return serialize_doc(issue_doc)
