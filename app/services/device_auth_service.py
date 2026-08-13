"""Short-lived, one-time device authorization for QR/device-code login.

The device receives a human-readable code plus a high-entropy secret.  The
code may safely appear in a QR URL; the secret never does.  Approval stores a
Supabase magic-link token briefly, and polling atomically consumes the whole
request so the token cannot be replayed through this API.
"""

from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timedelta, timezone

import httpx
from pymongo import ReturnDocument

from app.config import settings


LIFETIME = timedelta(minutes=10)
_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


async def start(db) -> dict:
    now = datetime.now(timezone.utc)
    # Mongo removes abandoned requests even if no new device ever starts. Index
    # creation is idempotent; in production this becomes a metadata no-op.
    await db.device_auth_requests.create_index("expiresAt", expireAfterSeconds=0)
    await db.device_auth_requests.create_index("deviceCode", unique=True)
    await db.device_auth_requests.delete_many({"expiresAt": {"$lte": now}})

    for _ in range(5):
        code = "".join(secrets.choice(_ALPHABET) for _ in range(8))
        secret = secrets.token_urlsafe(32)
        if not await db.device_auth_requests.find_one({"deviceCode": code}):
            await db.device_auth_requests.insert_one(
                {
                    "deviceCode": code,
                    "secretHash": _hash(secret),
                    "status": "pending",
                    "createdAt": now,
                    "expiresAt": now + LIFETIME,
                }
            )
            return {
                "deviceCode": code,
                "deviceSecret": secret,
                "expiresIn": int(LIFETIME.total_seconds()),
                "verificationUrl": f"{settings.APP_BASE_URL.rstrip('/')}/device-login/{code}",
            }
    raise RuntimeError("Could not allocate a device code")


async def approve(db, code: str, email: str) -> dict:
    now = datetime.now(timezone.utc)
    normalized = code.strip().upper().replace("-", "")
    if not email:
        return {"ok": False, "reason": "Signed-in account has no email address"}
    request = await db.device_auth_requests.find_one(
        {
            "deviceCode": normalized,
            "status": "pending",
            "expiresAt": {"$gt": now},
        }
    )
    if not request:
        return {"ok": False, "reason": "Code not found or expired"}

    key = settings.SUPABASE_SERVICE_ROLE_KEY
    if not key:
        raise RuntimeError("Device login is not configured on the server")

    url = f"{settings.SUPABASE_URL.rstrip('/')}/auth/v1/admin/generate_link"
    async with httpx.AsyncClient(timeout=10) as client:
        response = await client.post(
            url,
            headers={"apikey": key, "Authorization": f"Bearer {key}"},
            json={"type": "magiclink", "email": email},
        )
    if response.status_code >= 400:
        raise RuntimeError("Supabase could not create a device login token")

    body = response.json()
    properties = body.get("properties") or {}
    token = properties.get("hashed_token")
    if not token:
        action_link = properties.get("action_link", "")
        try:
            from urllib.parse import parse_qs, urlparse

            token = parse_qs(urlparse(action_link).query).get("token", [None])[0]
        except Exception:
            token = None
    if not token:
        raise RuntimeError("Supabase returned an unusable device login token")

    updated = await db.device_auth_requests.update_one(
        {"_id": request["_id"], "status": "pending"},
        {
            "$set": {
                "status": "approved",
                "email": email,
                "tokenHash": token,
                "approvedAt": now,
            }
        },
    )
    return {"ok": updated.modified_count == 1}


async def poll(db, code: str, secret: str) -> dict:
    now = datetime.now(timezone.utc)
    match = {
        "deviceCode": code.strip().upper().replace("-", ""),
        "secretHash": _hash(secret),
        "expiresAt": {"$gt": now},
    }
    request = await db.device_auth_requests.find_one(match, {"status": 1})
    if not request:
        return {"status": "expired"}
    if request.get("status") != "approved":
        return {"status": "pending"}

    consumed = await db.device_auth_requests.find_one_and_delete(
        {**match, "status": "approved"},
        projection={"email": 1, "tokenHash": 1},
        return_document=ReturnDocument.BEFORE,
    )
    if not consumed:
        return {"status": "expired"}
    return {
        "status": "approved",
        "email": consumed.get("email", ""),
        "tokenHash": consumed.get("tokenHash", ""),
    }
