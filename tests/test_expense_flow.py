"""Regression tests for the expense-creation → visibility flow.

These pin the contract the app's UI relies on:
* A personal ("normal") expense created without explicit payShares must still
  belong to its creator, so it shows up in the personal list and dashboard.
* A group expense created the way the split screen sends it (shares +
  payShares + type=group + eventId) must land in the event's expenses and
  its dues.
"""

import pytest


def test_personal_expense_without_payshares_is_visible(client):
    # Exactly the payload the manual "Add Expense" form sends: no payShares.
    resp = client.post(
        "/db/expense",
        json={
            "expenseName": "Solo lunch",
            "amount": 250.0,
            "category": "Food",
            "currency": "INR",
            "type": "normal",
            "date": "2026-07-20T00:00:00Z",
        },
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["success"] == "true"

    # It must appear in the user's personal list...
    listed = client.post("/db/expenses/personal", json={}).json()
    assert any(e.get("expenseName") == "Solo lunch" for e in listed)

    # ...and in the dashboard's personal total.
    summary = client.post(
        "/db/dashboard/summary",
        json={"startDate": "2026-07-01T00:00:00Z", "endDate": "2026-07-31T00:00:00Z"},
    ).json()
    assert summary["personal_expenses"] >= 250.0


def test_group_expense_from_split_screen_shape(client):
    account = client.get("/db/user/account").json()
    uid = account["id"]

    event = client.post(
        "/db/event",
        json={"eventName": "Trip", "users": [uid], "currency": "INR"},
    ).json()
    event_id = event["id"]

    # Split-screen equal-split shape: current user pays, shares carry eventId.
    resp = client.post(
        "/db/expense",
        json={
            "expenseName": "Hotel",
            "amount": 100.0,
            "category": "Accommodation",
            "currency": "INR",
            "type": "group",
            "eventId": event_id,
            "shares": [{"userId": uid, "amount": 100.0, "eventId": event_id}],
            "payShares": [{"userId": uid, "amount": 100.0}],
            "date": "2026-07-20T00:00:00Z",
        },
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["success"] == "true"

    # The expense is attached to the event.
    event_expenses = client.get(f"/db/event/{event_id}/expenses").json()
    names = [e["expenseName"] for e in event_expenses["expenses"]]
    assert "Hotel" in names
    hotel = next(e for e in event_expenses["expenses"] if e["expenseName"] == "Hotel")
    assert hotel["paidByName"] == account["name"]


def test_editing_a_personal_expense_keeps_its_split(client):
    """The edit form sends no shares/payShares. A plain model_dump emitted them
    as empty lists, which the service wrote — erasing the split and dropping
    the expense out of every list that queries on payShares."""
    created = client.post(
        "/db/expense",
        json={
            "expenseName": "Editable lunch",
            "amount": 200.0,
            "category": "Food",
            "currency": "INR",
            "type": "normal",
            "date": "2026-07-20T00:00:00Z",
        },
    ).json()
    expense_id = created["data"]["id"]

    resp = client.put(
        f"/db/expense/{expense_id}",
        json={
            "expenseName": "Editable lunch",
            "amount": 275.0,
            "category": "Food",
            "description": "went back for dessert",
        },
    )
    assert resp.status_code == 200, resp.text

    detail = client.get(f"/db/expense/{expense_id}").json()
    assert detail["amount"] == 275.0
    assert detail["payShares"], "edit wiped payShares"
    assert detail["shares"], "edit wiped shares"
    # Still its own type, and still in the personal list.
    assert detail["type"] == "normal"
    listed = client.post("/db/expenses/personal", json={}).json()
    assert any(e["id"] == expense_id for e in listed)
