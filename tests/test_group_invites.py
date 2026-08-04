"""Group join links, name-only placeholder members, and claiming a placeholder."""

import pytest
from bson import ObjectId
from motor.motor_asyncio import AsyncIOMotorClient

from app.auth import get_current_user
from app.config import settings
from app.main import app
from conftest import mock_get_current_user

JOINER_UID = "joiner_uid_invites"
JOINER_EMAIL = "joiner@example.com"


async def _joiner_identity():
    return {"uid": JOINER_UID, "email": JOINER_EMAIL, "name": "Joiner"}


def _direct_db():
    c = AsyncIOMotorClient(settings.MONGODB_URI)
    return c, c[settings.DB_NAME]


async def _register_joiner(db):
    await db.users.delete_many({"firebaseUid": JOINER_UID})
    await db.users.insert_one(
        {
            "firebaseUid": JOINER_UID,
            "email": JOINER_EMAIL,
            "name": "Joiner",
            "friendCode": "JOIN99AB",
            "friends": [],
            "role": "user",
        }
    )


def _create_group(client, name, extra_members=None):
    account = client.get("/db/user/account").json()
    resp = client.post(
        "/db/event",
        json={
            "eventName": name,
            "users": [account["id"], *(extra_members or [])],
            "currency": "INR",
        },
    )
    assert resp.status_code == 200, resp.text
    return account, resp.json()


def test_member_added_by_name_only(client):
    _, event = _create_group(client, "Trip", [{"name": "Sam"}])
    sam = next(u for u in event["users"] if u["name"] == "Sam")
    assert sam["email"] == ""
    assert sam["isPlaceholder"] is True


def test_member_added_by_name_and_email_is_a_normal_invite(client):
    _, event = _create_group(
        client, "Dinner", [{"name": "Riya", "email": "riya_invite@example.com"}]
    )
    riya = next(u for u in event["users"] if u["name"] == "Riya")
    assert riya["email"] == "riya_invite@example.com"
    # Email-backed members are invitees, not claimable slots.
    assert riya["isPlaceholder"] is False


def test_invite_code_is_stable_until_rotated(client):
    _, event = _create_group(client, "Stable")
    first = client.get(f"/db/event/{event['id']}/invite").json()["joinCode"]
    assert client.get(f"/db/event/{event['id']}/invite").json()["joinCode"] == first

    rotated = client.post(f"/db/event/{event['id']}/invite").json()["joinCode"]
    assert rotated != first
    # The old link stops resolving the moment a new code is issued.
    assert client.get(f"/db/join/{first}").status_code == 404
    assert client.get(f"/db/join/{rotated}").status_code == 200


def test_preview_is_public_but_reveals_no_members(client):
    _, event = _create_group(client, "Preview Me", [{"name": "Ghost"}])
    code = client.get(f"/db/event/{event['id']}/invite").json()["joinCode"]

    body = client.get(f"/db/join/{code}").json()
    assert body["eventName"] == "Preview Me"
    assert body["memberCount"] == 2
    assert "users" not in body and "members" not in body


def test_disabled_link_stops_working(client):
    _, event = _create_group(client, "Revoked")
    code = client.get(f"/db/event/{event['id']}/invite").json()["joinCode"]
    assert client.delete(f"/db/event/{event['id']}/invite").status_code == 200
    assert client.get(f"/db/join/{code}").status_code == 404
    assert client.post(f"/db/join/{code}", json={}).status_code == 404


@pytest.mark.asyncio
async def test_join_by_link_adds_member_and_friends_them(client):
    _, event = _create_group(client, "Joinable")
    code = client.get(f"/db/event/{event['id']}/invite").json()["joinCode"]

    mongo, db = _direct_db()
    try:
        await _register_joiner(db)
        app.dependency_overrides[get_current_user] = _joiner_identity
        try:
            resp = client.post(f"/db/join/{code}", json={})
            assert resp.status_code == 200, resp.text
            assert resp.json()["alreadyMember"] is False
            # Second use of the same link is a no-op, not a duplicate member.
            assert client.post(f"/db/join/{code}", json={}).json()["alreadyMember"]

            joined = client.get(f"/db/event/{event['id']}").json()
            assert JOINER_EMAIL in [u["email"] for u in joined["users"]]
        finally:
            app.dependency_overrides[get_current_user] = mock_get_current_user

        # Joining makes everyone friends, so expenses can be split afterwards.
        joiner = await db.users.find_one({"firebaseUid": JOINER_UID})
        creator = await db.users.find_one({"_id": ObjectId(event["createdBy"])})
        assert creator["_id"] in joiner.get("friends", [])
        assert joiner["_id"] in creator.get("friends", [])
    finally:
        mongo.close()


@pytest.mark.asyncio
async def test_claiming_a_placeholder_transfers_its_shares(client):
    account, event = _create_group(client, "Claimable", [{"name": "Sam"}])
    event_id = event["id"]
    sam_id = next(u["id"] for u in event["users"] if u["name"] == "Sam")

    # Sam owes half of a bill the creator paid for.
    client.post(
        "/db/expense",
        json={
            "expenseName": "Cab", "amount": 100.0, "category": "Travel",
            "type": "group", "eventId": event_id, "currency": "INR",
            "shares": [
                {"userId": account["id"], "amount": 50.0, "eventId": event_id},
                {"userId": sam_id, "amount": 50.0, "eventId": event_id},
            ],
            "payShares": [{"userId": account["id"], "amount": 100.0}],
        },
    )
    code = client.get(f"/db/event/{event_id}/invite").json()["joinCode"]

    mongo, db = _direct_db()
    try:
        await _register_joiner(db)
        app.dependency_overrides[get_current_user] = _joiner_identity
        try:
            claimable = client.get(f"/db/join/{code}/claimable").json()["members"]
            assert [m["name"] for m in claimable] == ["Sam"]

            resp = client.post(f"/db/join/{code}", json={"claimMemberId": sam_id})
            assert resp.status_code == 200, resp.text
            assert resp.json()["claimed"] is True

            # The slot is gone and the joiner inherited its debt.
            assert client.get(f"/db/join/{code}/claimable").json()["members"] == []
            dues = client.get(f"/db/user/event/{event_id}/dues").json()
        finally:
            app.dependency_overrides[get_current_user] = mock_get_current_user

        assert await db.users.find_one({"_id": ObjectId(sam_id)}) is None
        joiner = await db.users.find_one({"firebaseUid": JOINER_UID})
        expense = await db.expense.find_one({"expenseName": "Cab"})
        assert joiner["_id"] in [s["userId"] for s in expense["shares"]]
        assert ObjectId(sam_id) not in [s["userId"] for s in expense["shares"]]
        assert dues  # the joiner now has a balance in the group
    finally:
        mongo.close()


@pytest.mark.asyncio
async def test_email_backed_member_cannot_be_claimed(client):
    _, event = _create_group(
        client, "Not Yours", [{"name": "Riya", "email": "riya_claim@example.com"}]
    )
    riya_id = next(u["id"] for u in event["users"] if u["name"] == "Riya")
    code = client.get(f"/db/event/{event['id']}/invite").json()["joinCode"]

    mongo, db = _direct_db()
    try:
        await _register_joiner(db)
        app.dependency_overrides[get_current_user] = _joiner_identity
        try:
            assert client.get(f"/db/join/{code}/claimable").json()["members"] == []
            resp = client.post(f"/db/join/{code}", json={"claimMemberId": riya_id})
            assert resp.status_code == 400
        finally:
            app.dependency_overrides[get_current_user] = mock_get_current_user
    finally:
        mongo.close()
