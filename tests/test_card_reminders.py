"""Credit-card bill reminders.

The scheduler runs on every instance, every hour, so the interesting property
is not "does it send" but "does it send exactly once".
"""

from datetime import date

import pytest
from bson import ObjectId
from motor.motor_asyncio import AsyncIOMotorClient

from app.config import settings
from app.services import card_service


def _direct_db():
    mongo = AsyncIOMotorClient(settings.MONGODB_URI)
    return mongo, mongo[settings.DB_NAME]


def test_next_due_date_rolls_into_next_month_once_the_day_has_passed():
    assert card_service.next_due_date(5, date(2026, 8, 3)) == date(2026, 8, 5)
    assert card_service.next_due_date(1, date(2026, 8, 3)) == date(2026, 9, 1)
    # Today counts as due, not passed.
    assert card_service.next_due_date(3, date(2026, 8, 3)) == date(2026, 8, 3)


def test_a_due_day_past_the_end_of_the_month_clamps():
    """A card due on the 31st is still due in February — issuers clamp, so
    do we, rather than skipping the month entirely."""
    assert card_service.next_due_date(31, date(2026, 2, 1)) == date(2026, 2, 28)
    assert card_service.next_due_date(31, date(2026, 4, 1)) == date(2026, 4, 30)


def test_december_rolls_over_the_year():
    assert card_service.next_due_date(5, date(2026, 12, 10)) == date(2027, 1, 5)


def test_card_crud_round_trip(client):
    created = client.post(
        "/db/cards",
        json={"name": "HDFC Regalia", "dueDay": 12, "last4": "4321", "currency": "INR"},
    )
    assert created.status_code == 200, created.text
    card = created.json()
    assert card["name"] == "HDFC Regalia"
    assert card["last4"] == "4321"
    assert card["dueDay"] == 12
    assert card["nextDueDate"]

    listed = client.get("/db/cards").json()
    assert any(c["id"] == card["id"] for c in listed)

    updated = client.put(
        f"/db/cards/{card['id']}",
        json={"name": "HDFC Regalia", "dueDay": 20, "remindersEnabled": False},
    ).json()
    assert updated["dueDay"] == 20
    assert updated["remindersEnabled"] is False

    assert client.delete(f"/db/cards/{card['id']}").status_code == 200
    assert all(c["id"] != card["id"] for c in client.get("/db/cards").json())


def test_an_invalid_due_day_is_rejected(client):
    resp = client.post("/db/cards", json={"name": "Bad", "dueDay": 45})
    assert resp.status_code == 400
    assert "between 1 and 31" in resp.json()["detail"]


def test_a_card_without_a_name_is_rejected(client):
    resp = client.post("/db/cards", json={"name": "   ", "dueDay": 5})
    assert resp.status_code == 400


def test_only_the_last_four_digits_are_kept(client):
    """We store a recognition hint, never a usable card number."""
    card = client.post(
        "/db/cards",
        json={"name": "Paranoid", "dueDay": 5, "last4": "4111111111111111"},
    ).json()
    assert card["last4"] == "1111"
    client.delete(f"/db/cards/{card['id']}")


def test_another_users_card_id_is_not_reachable(client):
    """Card routes are scoped by owner, so a known id from another account
    matches nothing rather than leaking or mutating it."""
    stranger_card = str(ObjectId())
    assert client.put(
        f"/db/cards/{stranger_card}", json={"name": "x", "dueDay": 5}
    ).status_code == 404
    assert client.delete(f"/db/cards/{stranger_card}").status_code == 404


@pytest.mark.asyncio
async def test_reminders_fire_at_ten_and_five_days_and_only_once(client):
    account = client.get("/db/user/account").json()
    card = client.post(
        "/db/cards", json={"name": "Reminder card", "dueDay": 20, "currency": "INR"}
    ).json()

    mongo, db = _direct_db()
    try:
        await db.card_reminder_log.delete_many({})
        # Only this card should be in play; the suite shares one database.
        await db.credit_cards.delete_many({"_id": {"$ne": ObjectId(card["id"])}})

        # 15 days out: nothing yet.
        assert await card_service.send_due_reminders(db, date(2026, 8, 5)) == 0

        # 10 days out: one reminder...
        assert await card_service.send_due_reminders(db, date(2026, 8, 10)) == 1
        # ...and the hourly loop running again the same day must not repeat it.
        assert await card_service.send_due_reminders(db, date(2026, 8, 10)) == 0

        # 5 days out is a separate reminder, so it fires on its own.
        assert await card_service.send_due_reminders(db, date(2026, 8, 15)) == 1
        assert await card_service.send_due_reminders(db, date(2026, 8, 15)) == 0

        # Next month's occurrence is a different due date, so it fires again.
        assert await card_service.send_due_reminders(db, date(2026, 9, 10)) == 1

        # Three distinct (card, due date, lead) sends: 10-day and 5-day for
        # August, then 10-day for September. The repeat calls added nothing.
        assert await db.card_reminder_log.count_documents({}) == 3
        assert account["email"]
    finally:
        await db.card_reminder_log.delete_many({})
        mongo.close()
    client.delete(f"/db/cards/{card['id']}")


@pytest.mark.asyncio
async def test_disabled_cards_are_skipped(client):
    card = client.post(
        "/db/cards",
        json={"name": "Muted", "dueDay": 20, "remindersEnabled": False},
    ).json()

    mongo, db = _direct_db()
    try:
        await db.card_reminder_log.delete_many({})
        await db.credit_cards.delete_many({"_id": {"$ne": ObjectId(card["id"])}})
        assert await card_service.send_due_reminders(db, date(2026, 8, 10)) == 0
    finally:
        await db.card_reminder_log.delete_many({})
        mongo.close()
    client.delete(f"/db/cards/{card['id']}")
