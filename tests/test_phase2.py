"""Regression tests for Phase 2 fixes: event editing, email invites, friend
removal data, and payment links."""

import pytest
from bson import ObjectId
from motor.motor_asyncio import AsyncIOMotorClient

from app.config import settings
from conftest import MOCK_USER_UID


def _direct_db():
    client = AsyncIOMotorClient(settings.MONGODB_URI)
    return client, client[settings.DB_NAME]


def test_event_edit_round_trips_members(client):
    account = client.get("/db/user/account").json()
    created = client.post(
        "/db/event",
        json={"eventName": "Editable", "users": [account["id"]], "currency": "INR"},
    ).json()
    event_id = created["id"]
    # The edit form re-sends members as emails; renaming must not wipe them or
    # trip the "cannot remove event creator" guard.
    emails = [u["email"] for u in created["users"]]
    resp = client.put(
        "/db/event",
        json={
            "id": event_id,
            "eventName": "Renamed",
            "users": emails,
            "currency": "INR",
        },
    )
    assert resp.status_code == 200, resp.text
    updated = resp.json()
    assert updated["eventName"] == "Renamed"
    assert len(updated["users"]) == len(created["users"])


def test_event_email_member_creates_phantom(client):
    account = client.get("/db/user/account").json()
    resp = client.post(
        "/db/event",
        json={
            "eventName": "With Invitee",
            "users": [account["id"], "invitee_phase2@example.com"],
            "currency": "INR",
        },
    )
    assert resp.status_code == 200, resp.text
    event = resp.json()
    assert len(event["users"]) == 2
    assert any(u["email"] == "invitee_phase2@example.com" for u in event["users"])


@pytest.mark.asyncio
async def test_friend_list_includes_friend_code(client):
    mongo, db = _direct_db()
    try:
        me = await db.users.find_one({"firebaseUid": MOCK_USER_UID})
        friend = await db.users.insert_one(
            {
                "firebaseUid": "friend_phase2_uid",
                "email": "friendp2@example.com",
                "name": "Friend P2",
                "friendCode": "P2CODE00",
                "friends": [me["_id"]],
            }
        )
        await db.users.update_one(
            {"_id": me["_id"]}, {"$addToSet": {"friends": friend.inserted_id}}
        )

        data = client.get("/db/user/friends").json()
        entry = next(
            (f for f in data["friendsList"] if f["id"] == str(friend.inserted_id)),
            None,
        )
        assert entry is not None
        assert entry["friendCode"] == "P2CODE00"

        await db.users.delete_one({"_id": friend.inserted_id})
        await db.users.update_one(
            {"_id": me["_id"]}, {"$pull": {"friends": friend.inserted_id}}
        )
    finally:
        mongo.close()


def test_payment_link_is_valid_upi(client):
    created = client.post(
        "/db/payment",
        json={"destination": "someone@okbank", "amount": 123.45, "note": "lunch split"},
    ).json()
    link = created["link"]
    page = client.get(f"/db/payment/{link}").json()
    assert page["upiLink"].startswith("upi://pay?")
    assert "cu=" in page["upiLink"]
    assert "pa=someone%40okbank" in page["upiLink"]  # URL-encoded '@'
