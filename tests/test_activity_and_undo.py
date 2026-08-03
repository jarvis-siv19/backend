"""Activity feed and undo.

The point of the feature is that a change someone else made to a shared
balance is both visible and reversible, so these tests assert on what the
*other* person can see and do, not just the actor.
"""

from datetime import datetime, timezone

import pytest
from motor.motor_asyncio import AsyncIOMotorClient

from app.config import settings


def _make_expense(client, name="Coffee", amount=120.0):
    resp = client.post(
        "/db/expense",
        json={
            "expenseName": name,
            "amount": amount,
            "category": "Food",
            "currency": "INR",
            "type": "normal",
            "date": "2026-07-20T00:00:00Z",
        },
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]["id"]


def _find_entry(client, entity_id, action):
    for entry in client.get("/db/activity").json():
        if entry["entityId"] == entity_id and entry["action"] == action:
            return entry
    raise AssertionError(f"no {action} entry for {entity_id}")


def test_creating_an_expense_is_logged(client):
    expense_id = _make_expense(client, "Logged lunch", 300.0)
    entry = _find_entry(client, expense_id, "create")
    assert entry["actorName"] == "You"
    assert entry["actionLabel"] == "added"
    assert "Logged lunch" in entry["summary"]
    assert entry["canUndo"] is True


def test_undoing_a_create_removes_the_expense(client):
    expense_id = _make_expense(client, "Mistake", 999.0)
    entry = _find_entry(client, expense_id, "create")

    resp = client.post(f"/db/activity/{entry['id']}/undo")
    assert resp.status_code == 200, resp.text
    assert client.get(f"/db/expense/{expense_id}").status_code == 404


def test_undoing_an_edit_restores_the_previous_values(client):
    expense_id = _make_expense(client, "Dinner", 500.0)

    client.put(
        f"/db/expense/{expense_id}",
        json={"expenseName": "Dinner (edited)", "amount": 900.0, "category": "Food"},
    )
    assert client.get(f"/db/expense/{expense_id}").json()["amount"] == 900.0

    entry = _find_entry(client, expense_id, "update")
    assert client.post(f"/db/activity/{entry['id']}/undo").status_code == 200

    restored = client.get(f"/db/expense/{expense_id}").json()
    assert restored["expenseName"] == "Dinner"
    assert restored["amount"] == 500.0


def test_undoing_a_delete_brings_the_expense_back(client):
    expense_id = _make_expense(client, "Deleted taxi", 250.0)
    assert client.delete(f"/db/expense/{expense_id}").status_code == 200
    assert client.get(f"/db/expense/{expense_id}").status_code == 404

    entry = _find_entry(client, expense_id, "delete")
    assert client.post(f"/db/activity/{entry['id']}/undo").status_code == 200

    restored = client.get(f"/db/expense/{expense_id}").json()
    assert restored["expenseName"] == "Deleted taxi"
    assert restored["amount"] == 250.0


def test_a_deleted_group_expense_is_relinked_to_its_event_on_undo(client):
    """The link from event to expense is a separate array; restoring the
    document alone would leave the expense orphaned and invisible."""
    uid = client.get("/db/user/account").json()["id"]
    event_id = client.post(
        "/db/event", json={"eventName": "Undo trip", "users": [uid], "currency": "INR"}
    ).json()["id"]

    expense_id = client.post(
        "/db/expense",
        json={
            "expenseName": "Group hotel",
            "amount": 400.0,
            "category": "Accommodation",
            "currency": "INR",
            "type": "group",
            "eventId": event_id,
            "shares": [{"userId": uid, "amount": 400.0}],
            "payShares": [{"userId": uid, "amount": 400.0}],
            "date": "2026-07-20T00:00:00Z",
        },
    ).json()["data"]["id"]

    client.delete(f"/db/expense/{expense_id}")
    listed = client.get(f"/db/event/{event_id}/expenses").json()["expenses"]
    assert all(e["expenseId"] != expense_id for e in listed)

    entry = _find_entry(client, expense_id, "delete")
    assert client.post(f"/db/activity/{entry['id']}/undo").status_code == 200

    listed = client.get(f"/db/event/{event_id}/expenses").json()["expenses"]
    assert any(e["expenseId"] == expense_id for e in listed)


def test_an_entry_cannot_be_undone_twice(client):
    expense_id = _make_expense(client, "Double undo", 100.0)
    entry = _find_entry(client, expense_id, "create")

    assert client.post(f"/db/activity/{entry['id']}/undo").status_code == 200
    second = client.post(f"/db/activity/{entry['id']}/undo")
    assert second.status_code == 400
    assert "already undone" in second.json()["detail"].lower()


def test_undo_refuses_when_the_expense_moved_on(client):
    """Restoring a stale snapshot would erase the newer edit without a trace —
    exactly the surprise the activity log exists to prevent."""
    expense_id = _make_expense(client, "Racy", 100.0)
    client.put(
        f"/db/expense/{expense_id}",
        json={"expenseName": "Racy v2", "amount": 200.0, "category": "Food"},
    )
    stale = _find_entry(client, expense_id, "update")

    client.put(
        f"/db/expense/{expense_id}",
        json={"expenseName": "Racy v3", "amount": 300.0, "category": "Food"},
    )

    resp = client.post(f"/db/activity/{stale['id']}/undo")
    assert resp.status_code == 409
    assert client.get(f"/db/expense/{expense_id}").json()["amount"] == 300.0


@pytest.mark.asyncio
async def test_activity_is_scoped_to_people_it_affected(client):
    """Someone else's solo expense must not appear in your feed."""
    # A separate client: the TestClient owns the app's loop, so the test body
    # cannot reuse the app's Motor connection.
    mongo = AsyncIOMotorClient(settings.MONGODB_URI)
    db = mongo[settings.DB_NAME]
    try:
        stranger = await db.users.insert_one(
            {
                "firebaseUid": "activity_stranger",
                "email": "stranger-activity@example.com",
                "name": "Stranger",
                "friends": [],
            }
        )
        expense = await db.expense.insert_one(
            {
                "expenseName": "Not yours",
                "amount": 10.0,
                "type": "normal",
                "createdBy": stranger.inserted_id,
                "shares": [{"userId": stranger.inserted_id, "amount": 10.0}],
                "payShares": [{"userId": stranger.inserted_id, "amount": 10.0}],
            }
        )
        entry = await db.audit_log.insert_one(
            {
                "entity": "expense",
                "entityId": expense.inserted_id,
                "action": "create",
                "actorId": stranger.inserted_id,
                "visibleTo": [stranger.inserted_id],
                "summary": "Not yours",
                "undoable": True,
                "undoneAt": None,
                "createdAt": datetime.now(timezone.utc),
            }
        )

        feed = client.get("/db/activity").json()
        assert all(e["entityId"] != str(expense.inserted_id) for e in feed)

        # ...and the undo endpoint must not confirm the entry even exists.
        resp = client.post(f"/db/activity/{entry.inserted_id}/undo")
        assert resp.status_code == 404
    finally:
        await db.users.delete_many({"firebaseUid": "activity_stranger"})
        await db.expense.delete_many({"expenseName": "Not yours"})
        await db.audit_log.delete_many({"summary": "Not yours"})
        mongo.close()
