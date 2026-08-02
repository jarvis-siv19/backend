"""Regression tests for the security review findings.

Each test fails against the pre-fix code. They cover, in order:
  1. /db/user/phantom returning another user's full document
  2. /db/user/invites returning password hashes and auth subjects
  3. expense creation with no event membership / arbitrary participants
  4. event creation pulling in an unrelated registered user
  5. personal-expense filters overwriting the ownership predicate
"""

import pytest
from bson import ObjectId
from motor.motor_asyncio import AsyncIOMotorClient

from app.config import settings
from conftest import MOCK_USER_UID

VICTIM_EMAIL = "victim@example.com"


@pytest.fixture
async def db():
    mongo = AsyncIOMotorClient(settings.MONGODB_URI)
    try:
        yield mongo[settings.DB_NAME]
    finally:
        mongo.close()


async def _make_victim(db, email=VICTIM_EMAIL):
    """A fully-fledged registered user unrelated to the caller."""
    await db.users.delete_many({"email": email})
    result = await db.users.insert_one(
        {
            "firebaseUid": "victim_uid_999",
            "email": email,
            "name": "Victim",
            "friendCode": "VICTIM01",
            "friends": [],
            "role": "user",
            # A legacy document that still carries the old hash.
            "password": "$argon2id$v=19$m=65536,t=3,p=4$legacyhash",
        }
    )
    return result.inserted_id


# ── 1. phantom lookup must not disclose another user's record ──────────────

@pytest.mark.asyncio
async def test_phantom_lookup_does_not_leak_victim_record(client, db):
    victim_id = await _make_victim(db)
    try:
        response = client.post("/db/user/phantom", json={"email": VICTIM_EMAIL})
        assert response.status_code == 200
        body = response.json()

        assert "password" not in body
        assert "friendCode" not in body
        assert "firebaseUid" not in body
        assert "friends" not in body
        # Only enough to attach a share.
        assert set(body) <= {"id", "name", "email", "wasCreated"}
    finally:
        await db.users.delete_one({"_id": victim_id})


# ── 2. invite list must not disclose credentials ───────────────────────────

@pytest.mark.asyncio
async def test_invites_list_does_not_leak_credentials(client, db):
    caller = await db.users.find_one({"firebaseUid": MOCK_USER_UID})
    invitee = await db.users.insert_one(
        {
            "firebaseUid": "invitee_uid_998",
            "email": "invitee@example.com",
            "name": "Invitee",
            "friendCode": "INVITE01",
            "friends": [],
            "role": "user",
            "invitedBy": caller["friendCode"],
            "password": "$argon2id$v=19$m=65536,t=3,p=4$legacyhash",
        }
    )
    try:
        response = client.get("/db/user/invites")
        assert response.status_code == 200
        rows = response.json()
        assert rows, "expected the seeded invitee"
        for row in rows:
            assert "password" not in row
            assert "firebaseUid" not in row
            assert "friendCode" not in row
    finally:
        await db.users.delete_one({"_id": invitee.inserted_id})


# ── 3. expenses may not name unrelated users ───────────────────────────────

@pytest.mark.asyncio
async def test_cannot_create_expense_naming_a_stranger(client, db):
    victim_id = await _make_victim(db)
    caller = await db.users.find_one({"firebaseUid": MOCK_USER_UID})
    try:
        response = client.post(
            "/db/expense",
            json={
                "expenseName": "Fabricated debt",
                "amount": 50000,
                "category": "General",
                "type": "friend",
                "shares": [{"userId": str(victim_id), "amount": 50000}],
                "payShares": [{"userId": str(caller["_id"]), "amount": 50000}],
            },
        )
        assert response.status_code == 403

        # And nothing was written against the victim.
        assert await db.expense.find_one({"shares.userId": victim_id}) is None
    finally:
        await db.users.delete_one({"_id": victim_id})


@pytest.mark.asyncio
async def test_cannot_add_expense_to_an_event_you_are_not_in(client, db):
    victim_id = await _make_victim(db)
    event = await db.event.insert_one(
        {
            "eventName": "Not yours",
            "users": [victim_id],
            "expenses": [],
            "currency": "INR",
            "createdBy": victim_id,
        }
    )
    caller = await db.users.find_one({"firebaseUid": MOCK_USER_UID})
    try:
        response = client.post(
            "/db/expense",
            json={
                "expenseName": "Injected",
                "amount": 100,
                "category": "General",
                "type": "group",
                "eventId": str(event.inserted_id),
                "shares": [{"userId": str(victim_id), "amount": 100}],
                "payShares": [{"userId": str(caller["_id"]), "amount": 100}],
            },
        )
        assert response.status_code == 403

        refreshed = await db.event.find_one({"_id": event.inserted_id})
        assert refreshed["expenses"] == []
    finally:
        await db.event.delete_one({"_id": event.inserted_id})
        await db.users.delete_one({"_id": victim_id})


# ── 4. events may not pull in unrelated registered users ───────────────────

@pytest.mark.asyncio
async def test_cannot_add_a_stranger_to_an_event(client, db):
    victim_id = await _make_victim(db)
    try:
        response = client.post(
            "/db/event",
            json={"eventName": "Attacker event", "users": [str(victim_id)]},
        )
        assert response.status_code == 403

        # No unconsented friendship was written into the victim's document.
        victim = await db.users.find_one({"_id": victim_id})
        assert victim["friends"] == []
    finally:
        await db.event.delete_many({"eventName": "Attacker event"})
        await db.users.delete_one({"_id": victim_id})


@pytest.mark.asyncio
async def test_can_still_invite_a_new_person_by_email(client, db):
    """The fix must not break the core invite-by-email flow."""
    new_email = "brand-new-invitee@example.com"
    await db.users.delete_many({"email": new_email})
    try:
        response = client.post(
            "/db/event",
            json={"eventName": "Trip", "users": [new_email]},
        )
        assert response.status_code == 200, response.text
        emails = {u["email"] for u in response.json()["users"]}
        assert new_email in emails
    finally:
        await db.event.delete_many({"eventName": "Trip"})
        await db.users.delete_many({"email": new_email})


# ── 5. personal-expense filters may not rewrite the ownership predicate ────

@pytest.mark.asyncio
async def test_filters_cannot_overwrite_ownership_predicate(client, db):
    victim_id = await _make_victim(db)
    foreign = await db.expense.insert_one(
        {
            "expenseName": "Victim private expense",
            "amount": 999,
            "type": "normal",
            "category": "General",
            # The document class the injection targeted: empty payShares.
            "payShares": [],
            "shares": [{"userId": victim_id, "amount": 999}],
            "createdBy": victim_id,
        }
    )
    try:
        response = client.post(
            "/db/expenses/personal",
            json={
                "filters": [
                    {"field": "payShares", "operator": "IN", "values": [[], None]},
                    {"field": "type", "operator": "IN", "values": ["normal"]},
                ]
            },
        )
        assert response.status_code == 200
        names = {e.get("expenseName") for e in response.json()}
        assert "Victim private expense" not in names
    finally:
        await db.expense.delete_one({"_id": foreign.inserted_id})
        await db.users.delete_one({"_id": victim_id})
