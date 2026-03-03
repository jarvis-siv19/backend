from fastapi import APIRouter, Depends, HTTPException
from motor.motor_asyncio import AsyncIOMotorDatabase
from app.auth import get_current_user
from app.database import get_db
from app.services import llm_service
from app.models.schemas import LLMExpenseRequest

router = APIRouter()


@router.post("/expense")
async def convert_expense(
    request: LLMExpenseRequest,
    current_user: dict = Depends(get_current_user),
    db: AsyncIOMotorDatabase = Depends(get_db),
):
    """
    Use LLM to parse a natural language expense description.
    Fetches the prompt from the database (with versioning) and sends to Gemini.
    """
    sentence = request.requestData.get("sentence", "")
    if not sentence:
        raise HTTPException(status_code=400, detail="Sentence is required")

    result = await llm_service.process_expense_from_text(db, sentence)
    return result
