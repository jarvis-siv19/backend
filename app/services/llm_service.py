import json
import traceback
from motor.motor_asyncio import AsyncIOMotorDatabase
from app.config import settings
from datetime import datetime, timezone
from .llm.provider_base import BaseLLMProvider
from .llm.google_provider import GoogleGeminiProvider
from .llm.ollama_provider import OllamaProvider


def get_llm_provider(model_name: str) -> BaseLLMProvider:
    """Factory to get the correct provider based on model name."""
    if model_name.startswith("gemini-"):
        return GoogleGeminiProvider()
    else:
        return OllamaProvider()


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
    Generate text content using generic LLM API.
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
        # Fetch active model from DB
        settings_doc = await db.settings.find_one({"_id": "llm_settings"})
        active_model = settings_doc.get("activeModel", settings.GEMINI_MODEL) if settings_doc else settings.GEMINI_MODEL

        provider = get_llm_provider(active_model)
        result_text, stats = await provider.generate_content(active_model, full_prompt)

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

        # Update model global stats
        await db.llm_stats.update_one(
            {"model": active_model},
            {
                "$inc": {
                    "totalRequests": 1,
                    "promptTokens": stats["prompt_tokens"],
                    "completionTokens": stats["completion_tokens"],
                    "totalTokens": stats["total_tokens"],
                },
                "$set": {"lastUsedAt": datetime.now(timezone.utc)}
            },
            upsert=True
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
