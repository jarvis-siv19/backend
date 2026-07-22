"""Security regression: a user who is not a participant/member must not be able
to read or delete another user's expenses or events (IDOR)."""

import pytest
from bson import ObjectId
from motor.motor_asyncio import AsyncIOMotorClient

from app.auth import get_current_user
from app.config import settings
from app.main import app

ATTACKER_UID = "attacker_uid_authz"
ATTACKER_EMAIL = "attacker@example.com"


async def _attacker_identity():
    return {"uid": ATTACKER_UID, "email": ATTACKER_EMAIL, "name": "Attacker"}


def _direct_db():
    c = AsyncIOMotorClient(settings.MONGODB_URI)
    return c, c[settings.DB_NAME]


@pytest.mark.asyncio
async def test_non_member_cannot_touch_others_event_or_expense(client):
    # Victim (the seeded mock user) creates an event + expense.
    account = client.get("/db/user/account").json()
    uid = account["id"]
    event = client.post(
        "/db/event",
        json={"eventName": "Private", "users": [uid], "currency": "INR"},
    ).json()
    event_id = event["id"]
    expense = client.post(
        "/db/expense",
        json={
            "expenseName": "Secret", "amount": 50.0, "category": "Food",
            "type": "group", "eventId": event_id, "currency": "INR",
            "shares": [{"userId": uid, "amount": 50.0, "eventId": event_id}],
            "payShares": [{"userId": uid, "amount": 50.0}],
        },
    ).json()
    expense_id = expense["data"]["id"]

    # Register a real attacker account (so the routes get past user lookup).
    mongo, db = _direct_db()
    try:
        await db.users.delete_many({"firebaseUid": ATTACKER_UID})
        await db.users.insert_one(
            {"firebaseUid": ATTACKER_UID, "email": ATTACKER_EMAIL,
             "name": "Attacker", "friends": [], "role": "user"}
        )

        app.dependency_overrides[get_current_user] = _attacker_identity
        try:
            assert client.get(f"/db/event/{event_id}").status_code == 404
            assert client.delete(f"/db/event/{event_id}").status_code == 404
            assert client.get(f"/db/event/{event_id}/expenses").status_code == 404
            assert client.get(f"/db/user/event/{event_id}/dues").status_code == 404
            assert client.get(f"/db/expense/{expense_id}").status_code == 404
            assert client.delete(f"/db/expense/{expense_id}").status_code == 403
        finally:
            # Restore the session-wide mock (conftest's override).
            from conftest import mock_get_current_user
            app.dependency_overrides[get_current_user] = mock_get_current_user

        # The victim's data is untouched and still reachable by the victim.
        assert client.get(f"/db/event/{event_id}").status_code == 200
        assert client.get(f"/db/expense/{expense_id}").status_code == 200
    finally:
        await db.users.delete_many({"firebaseUid": ATTACKER_UID})
        mongo.close()
