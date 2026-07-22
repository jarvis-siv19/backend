import secrets
import string
from datetime import datetime, timedelta, timezone
from urllib.parse import quote
from bson import ObjectId
from motor.motor_asyncio import AsyncIOMotorDatabase
from app.services import fx_service
from app.services.user_service import serialize_doc


def generate_link(length: int = 256) -> str:
    """Generate a random URL-safe link."""
    characters = string.ascii_letters + string.digits
    return "".join(secrets.choice(characters) for _ in range(length))


async def create_page(
    db: AsyncIOMotorDatabase, user_id: str, request_data: dict
) -> dict:
    """Create a payment page with UPI link."""
    upi_id = request_data.get("destination", "")
    try:
        expiry = int(request_data.get("expiry", 24))
    except (TypeError, ValueError):
        expiry = 24
    amount = request_data.get("amount", 0)
    note = request_data.get("note", "")

    now = datetime.now(timezone.utc)
    link = generate_link()

    # The payee name and currency come from the requester's account, and every
    # field is URL-encoded so notes/UPI ids with spaces or symbols don't break
    # the link. The `upi://` scheme is required for a UPI app to open it.
    payer = await db.users.find_one(
        {"_id": ObjectId(user_id)}, {"name": 1, "defaultCurrency": 1}
    )
    payee_name = (payer or {}).get("name") or "SplitLLM User"
    currency = fx_service.normalize_currency(
        request_data.get("currency") or (payer or {}).get("defaultCurrency")
    )
    upi_link = (
        "upi://pay?"
        f"pa={quote(str(upi_id))}"
        f"&pn={quote(payee_name)}"
        f"&tn={quote(str(note))}"
        f"&am={quote(str(amount))}"
        f"&cu={currency}"
    )

    page = {
        "userId": ObjectId(user_id),
        "upiId": upi_id,
        "upiLink": upi_link,
        "link": link,
        "amount": float(amount),
        "currency": currency,
        "note": note,
        "createdAt": now,
        "expiryAt": now + timedelta(hours=expiry),
    }

    result = await db.payment_page.insert_one(page)
    page["_id"] = result.inserted_id
    return serialize_doc(page)


async def view_page(db: AsyncIOMotorDatabase, link: str) -> dict | None:
    """View a payment page by its link (public, no auth)."""
    pipeline = [
        {"$match": {"link": link}},
        {
            "$lookup": {
                "from": "users",
                "localField": "userId",
                "foreignField": "_id",
                "as": "user",
            }
        },
        {"$unwind": {"path": "$user", "preserveNullAndEmptyArrays": True}},
        {
            "$project": {
                "_id": 0,
                "upiId": 1,
                "upiLink": 1,
                "amount": 1,
                "currency": 1,
                "note": 1,
                "expiryAt": 1,
                "userName": {"$ifNull": ["$user.name", "Unknown"]},
            }
        },
    ]

    results = await db.payment_page.aggregate(pipeline).to_list(length=1)
    if not results:
        return None

    page = results[0]
    # Convert datetime fields
    if isinstance(page.get("expiryAt"), datetime):
        page["expiryAt"] = page["expiryAt"].isoformat()

    return page


async def view_pages(db: AsyncIOMotorDatabase, user_id: str) -> list:
    """View all payment pages for a user."""
    pages = (
        await db.payment_page.find({"userId": ObjectId(user_id)})
        .sort("createdAt", -1)
        .to_list(length=None)
    )
    return [serialize_doc(p) for p in pages]
