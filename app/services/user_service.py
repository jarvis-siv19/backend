import random
import string
from datetime import datetime, timezone
from bson import ObjectId
from motor.motor_asyncio import AsyncIOMotorDatabase
from argon2 import PasswordHasher

ph = PasswordHasher()


def generate_friend_code(length: int = 8) -> str:
    """Generate a unique alphanumeric friend code."""
    num_digits = 2
    num_letters = length - num_digits
    digits = "".join(random.choice(string.digits) for _ in range(num_digits))
    letters = "".join(
        random.choice(string.ascii_uppercase) for _ in range(num_letters)
    )
    code_list = list(digits + letters)
    random.shuffle(code_list)
    return "".join(code_list)


def serialize_doc(doc: dict) -> dict:
    """Convert MongoDB document to JSON-serializable dict."""
    if doc is None:
        return None
    result = {}
    for key, value in doc.items():
        if key == "_id":
            result["id"] = str(value)
        elif isinstance(value, ObjectId):
            result[key] = str(value)
        elif isinstance(value, datetime):
            result[key] = value.isoformat()
        elif isinstance(value, list):
            result[key] = [
                str(v) if isinstance(v, ObjectId) else v for v in value
            ]
        else:
            result[key] = value
    return result


async def find_or_create_user(
    db: AsyncIOMotorDatabase, uid: str, email: str, name: str = "", password: str = ""
) -> dict:
    """Find user by Firebase UID, or create one if not exists."""
    user = await db.users.find_one({"firebaseUid": uid})
    if user:
        return serialize_doc(user)

    # Also check by email for backward compatibility
    user = await db.users.find_one({"email": email})
    if user:
        # Link Firebase UID to existing user
        await db.users.update_one(
            {"_id": user["_id"]}, {"$set": {"firebaseUid": uid}}
        )
        user["firebaseUid"] = uid
        return serialize_doc(user)

    # Create new user
    now = datetime.now(timezone.utc)
    friend_code = generate_friend_code(8)

    # Ensure friend code is unique
    while await db.users.find_one({"friendCode": friend_code}):
        friend_code = generate_friend_code(8)

    password_hash = ""
    if password:
        password_hash = ph.hash(password)

    new_user = {
        "firebaseUid": uid,
        "email": email,
        "name": name or email.split("@")[0],
        "friendCode": friend_code,
        "upiId": "",
        "friends": [],
        "password": password_hash,
        "createdAt": now,
        "updatedAt": now,
    }
    result = await db.users.insert_one(new_user)
    new_user["_id"] = result.inserted_id
    return serialize_doc(new_user)


async def get_user_by_firebase_uid(db: AsyncIOMotorDatabase, uid: str) -> dict | None:
    """Get user by Firebase UID."""
    user = await db.users.find_one({"firebaseUid": uid})
    return serialize_doc(user) if user else None


async def get_user_by_id(db: AsyncIOMotorDatabase, user_id: str) -> dict | None:
    """Get user by MongoDB ObjectId string."""
    try:
        user = await db.users.find_one({"_id": ObjectId(user_id)})
        return serialize_doc(user) if user else None
    except Exception:
        return None


async def get_user_name_by_id(db: AsyncIOMotorDatabase, user_id: str) -> str:
    """Get just the user's name by ID."""
    user = await get_user_by_id(db, user_id)
    return user["name"] if user else "Unknown"


async def get_user_account(db: AsyncIOMotorDatabase, uid: str) -> dict | None:
    """Get full account info for the current user."""
    user = await db.users.find_one({"firebaseUid": uid})
    if not user:
        return None
    return {
        "id": str(user["_id"]),
        "email": user.get("email", ""),
        "name": user.get("name", ""),
        "friendCode": user.get("friendCode", ""),
        "upiId": user.get("upiId", ""),
        "uuid": user.get("friendCode", ""),
    }


async def update_user_account(
    db: AsyncIOMotorDatabase, uid: str, data: dict
) -> bool:
    """Update user account fields (name, upiId)."""
    update_fields = {"updatedAt": datetime.now(timezone.utc)}
    if "name" in data and data["name"]:
        update_fields["name"] = data["name"]
    if "upiId" in data and data["upiId"] is not None:
        update_fields["upiId"] = data["upiId"]

    result = await db.users.update_one(
        {"firebaseUid": uid}, {"$set": update_fields}
    )
    return result.modified_count > 0 or result.matched_count > 0


async def change_password(
    db: AsyncIOMotorDatabase, uid: str, password: str
) -> dict:
    """Update stored password hash."""
    password_hash = ph.hash(password)
    await db.users.update_one(
        {"firebaseUid": uid},
        {"$set": {"password": password_hash, "updatedAt": datetime.now(timezone.utc)}},
    )
    return {"message": "Password updated successfully"}


async def get_user_internal_id(db: AsyncIOMotorDatabase, uid: str) -> str | None:
    """Get the MongoDB ObjectId string for a Firebase UID user."""
    user = await db.users.find_one({"firebaseUid": uid}, {"_id": 1})
    return str(user["_id"]) if user else None
