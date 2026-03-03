import json
import traceback
from motor.motor_asyncio import AsyncIOMotorDatabase
from google import genai
from app.config import settings


def _get_client() -> genai.Client:
    """Get a configured Gemini API client."""
    return genai.Client(api_key=settings.GEMINI_API_KEY)


async def get_prompt(db: AsyncIOMotorDatabase, prompt_type: str) -> dict | None:
    """Get the latest prompt from the database by type, ordered by version."""
    prompt = await db.prompts.find_one(
        {"type": prompt_type},
        sort=[("version", -1)],
    )
    return prompt


async def generate_text_content(
    db: AsyncIOMotorDatabase, prompt_type: str, user_input: str
) -> tuple[str | None, str | None]:
    """
    Generate text content using Google Gemini API.
    Fetches the prompt from the database and appends user input.
    Returns (result, error).
    """
    # Get prompt from database
    prompt_doc = await get_prompt(db, prompt_type)
    if not prompt_doc:
        return None, f"Prompt type '{prompt_type}' not found in database"

    prompt_text = prompt_doc.get("prompt", "")
    full_prompt = prompt_text + user_input

    try:
        client = _get_client()
        response = client.models.generate_content(
            model=settings.GEMINI_MODEL,
            contents=full_prompt,
        )

        result_text = response.text
        # Clean up the response
        result_text = (
            result_text.replace("`", "")
            .replace("json", "")
            .replace("JSON", "")
            .strip()
        )

        # Update prompt hit counters
        await db.prompts.update_one(
            {"_id": prompt_doc["_id"]},
            {"$inc": {"hits": 1, "successHits": 1}},
        )

        return result_text, None

    except Exception as e:
        traceback.print_exc()
        # Update failure counter
        if prompt_doc:
            await db.prompts.update_one(
                {"_id": prompt_doc["_id"]},
                {"$inc": {"hits": 1, "failureHits": 1}},
            )
        return None, str(e)


async def process_expense_from_text(
    db: AsyncIOMotorDatabase, sentence: str
) -> dict:
    """
    Parse a natural language expense description using LLM.
    Returns structured expense data or error.
    """
    result, error = await generate_text_content(db, "chat", sentence)

    if error:
        return {
            "message": f"Exception while calling Gemini API: {error}",
            "llmoutput": {},
        }

    try:
        json_response = json.loads(result)
        # Normalize keys to lowercase
        if isinstance(json_response, list):
            json_array = [
                {k.lower(): v for k, v in item.items()} for item in json_response
            ]
        else:
            json_array = [{k.lower(): v for k, v in json_response.items()}]

        return {
            "message": "Expense Processed Successfully",
            "llmoutput": json_array,
        }
    except json.JSONDecodeError:
        return {
            "message": "Failed to parse LLM response as JSON",
            "llmoutput": {},
            "raw": result,
        }
