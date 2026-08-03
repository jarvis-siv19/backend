"""Settle-up confirmation.

The rule: whoever *owes* the money can only claim to have paid it, so the other
side has to confirm. Whoever is *owed* can settle outright — they are the only
person who could dispute it. Nothing is recorded, and no "settled" email is
sent, until the settlement actually completes.
"""

import pytest
from bson import ObjectId
from motor.motor_asyncio import AsyncIOMotorClient

from app.auth import get_current_user
from app.config import settings
from app.main import app

FRIEND_UID = "settle_friend_uid"
FRIEND_EMAIL = "settle-friend@example.com"


async def _friend_identity():
    return {"uid": FRIEND_UID, "email": FRIEND_EMAIL, "name": "Settle Friend"}


def _direct_db():
    mongo = AsyncIOMotorClient(settings.MONGODB_URI)
    return mongo, mongo[settings.DB_NAME]


async def _make_friend(user_id: str) -> str:
    """Create a second registered account already friends with the test user."""
    mongo, db = _direct_db()
    try:
        await db.users.delete_many({"firebaseUid": FRIEND_UID})
        result = await db.users.insert_one(
            {
                "firebaseUid": FRIEND_UID,
                "email": FRIEND_EMAIL,
                "name": "Settle Friend",
                "friendCode": "SETTLE01",
                "defaultCurrency": "INR",
                "friends": [ObjectId(user_id)],
                "role": "user",
            }
        )
        await db.users.update_one(
            {"_id": ObjectId(user_id)},
            {"$addToSet": {"friends": result.inserted_id}},
        )
        return str(result.inserted_id)
    finally:
        mongo.close()


async def _clear_between(user_id: str, friend_id: str) -> None:
    mongo, db = _direct_db()
    try:
        await db.expense.delete_many(
            {"payShares.userId": {"$in": [ObjectId(user_id), ObjectId(friend_id)]}}
        )
        await db.settlement_request.delete_many({})
    finally:
        mongo.close()


def _as_friend():
    app.dependency_overrides[get_current_user] = _friend_identity


def _as_self():
    from tests.conftest import mock_get_current_user

    app.dependency_overrides[get_current_user] = mock_get_current_user


@pytest.mark.asyncio
async def test_debtor_settling_needs_the_creditor_to_confirm(client):
    user_id = client.get("/db/user/account").json()["id"]
    friend_id = await _make_friend(user_id)
    await _clear_between(user_id, friend_id)

    # The friend pays for something we both share, so we owe them.
    _as_friend()
    try:
        client.post(
            "/db/expense",
            json={
                "expenseName": "Friend paid dinner",
                "amount": 1000.0,
                "category": "Food",
                "currency": "INR",
                "type": "friend",
                "shares": [
                    {"userId": user_id, "amount": 500.0},
                    {"userId": friend_id, "amount": 500.0},
                ],
                "payShares": [{"userId": friend_id, "amount": 1000.0}],
                "date": "2026-07-20T00:00:00Z",
            },
        )
    finally:
        _as_self()

    dues = client.get(f"/db/user/expense/friend/{friend_id}").json()
    assert dues["overallWhoOwes"] == "user"

    # We owe, so our settle-up only *claims* payment.
    resp = client.post(f"/db/user/expense/friend/{friend_id}/settleup").json()
    assert resp["status"] == "pending"
    settlement_id = resp["settlementId"]

    # Nothing has moved yet.
    still = client.get(f"/db/user/expense/friend/{friend_id}").json()
    assert still["overallOweAmount"] == pytest.approx(500.0, abs=0.5)

    # We cannot confirm our own claim.
    assert client.post(f"/db/settlements/{settlement_id}/confirm").json()["success"] == "false"

    # The creditor confirms, and only then does the balance clear.
    _as_friend()
    try:
        pending = client.get("/db/settlements").json()
        assert any(p["id"] == settlement_id and p["awaitingYou"] for p in pending)
        confirmed = client.post(f"/db/settlements/{settlement_id}/confirm").json()
        assert confirmed["status"] == "settled"
    finally:
        _as_self()

    cleared = client.get(f"/db/user/expense/friend/{friend_id}").json()
    assert cleared["overallOweAmount"] == pytest.approx(0.0, abs=0.5)


@pytest.mark.asyncio
async def test_creditor_settling_completes_immediately(client):
    user_id = client.get("/db/user/account").json()["id"]
    friend_id = await _make_friend(user_id)
    await _clear_between(user_id, friend_id)

    # We paid, so the friend owes us.
    client.post(
        "/db/expense",
        json={
            "expenseName": "We paid dinner",
            "amount": 800.0,
            "category": "Food",
            "currency": "INR",
            "type": "friend",
            "shares": [
                {"userId": user_id, "amount": 400.0},
                {"userId": friend_id, "amount": 400.0},
            ],
            "payShares": [{"userId": user_id, "amount": 800.0}],
            "date": "2026-07-20T00:00:00Z",
        },
    )

    resp = client.post(f"/db/user/expense/friend/{friend_id}/settleup").json()
    assert resp["status"] == "settled"

    cleared = client.get(f"/db/user/expense/friend/{friend_id}").json()
    assert cleared["overallOweAmount"] == pytest.approx(0.0, abs=0.5)


@pytest.mark.asyncio
async def test_settling_with_nothing_outstanding_is_refused(client):
    user_id = client.get("/db/user/account").json()["id"]
    friend_id = await _make_friend(user_id)
    await _clear_between(user_id, friend_id)

    resp = client.post(f"/db/user/expense/friend/{friend_id}/settleup").json()
    assert resp["success"] == "false"
    assert "nothing to settle" in resp["message"].lower()


@pytest.mark.asyncio
async def test_a_second_request_does_not_stack_another_pending_settlement(client):
    user_id = client.get("/db/user/account").json()["id"]
    friend_id = await _make_friend(user_id)
    await _clear_between(user_id, friend_id)

    _as_friend()
    try:
        client.post(
            "/db/expense",
            json={
                "expenseName": "Friend paid again",
                "amount": 600.0,
                "category": "Food",
                "currency": "INR",
                "type": "friend",
                "shares": [
                    {"userId": user_id, "amount": 300.0},
                    {"userId": friend_id, "amount": 300.0},
                ],
                "payShares": [{"userId": friend_id, "amount": 600.0}],
                "date": "2026-07-20T00:00:00Z",
            },
        )
    finally:
        _as_self()

    first = client.post(f"/db/user/expense/friend/{friend_id}/settleup").json()
    assert first["status"] == "pending"

    second = client.post(f"/db/user/expense/friend/{friend_id}/settleup").json()
    assert second["success"] == "false"
    assert second["settlementId"] == first["settlementId"]


@pytest.mark.asyncio
async def test_the_creditor_can_decline_a_claimed_payment(client):
    user_id = client.get("/db/user/account").json()["id"]
    friend_id = await _make_friend(user_id)
    await _clear_between(user_id, friend_id)

    _as_friend()
    try:
        client.post(
            "/db/expense",
            json={
                "expenseName": "Friend paid, disputed",
                "amount": 400.0,
                "category": "Food",
                "currency": "INR",
                "type": "friend",
                "shares": [
                    {"userId": user_id, "amount": 200.0},
                    {"userId": friend_id, "amount": 200.0},
                ],
                "payShares": [{"userId": friend_id, "amount": 400.0}],
                "date": "2026-07-20T00:00:00Z",
            },
        )
    finally:
        _as_self()

    settlement_id = client.post(
        f"/db/user/expense/friend/{friend_id}/settleup"
    ).json()["settlementId"]

    _as_friend()
    try:
        assert (
            client.post(f"/db/settlements/{settlement_id}/reject").json()["success"]
            == "true"
        )
    finally:
        _as_self()

    # The debt stands, and a fresh claim can be made.
    still = client.get(f"/db/user/expense/friend/{friend_id}").json()
    assert still["overallOweAmount"] == pytest.approx(200.0, abs=0.5)
    assert (
        client.post(f"/db/user/expense/friend/{friend_id}/settleup").json()["status"]
        == "pending"
    )
