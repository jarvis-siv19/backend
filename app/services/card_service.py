"""Credit-card bill reminders.

A card is stored as a name, an optional last-four, and a **day of the month**
the bill falls due — not a fixed date, because the bill recurs. Reminders go
out 10 and 5 days before each occurrence.

We deliberately store nothing that could pay a bill: no card number beyond the
last four digits the user types for their own recognition, no credentials, no
issuer link. This is a calendar, not a payment integration.

Sending is driven by :func:`send_due_reminders`, which the app calls once a day
from a background loop. Two properties make that safe to run repeatedly and
from more than one instance:

* **Idempotent.** Each (card, due date, lead time) can be sent once, enforced
  by a unique index on ``card_reminder_log`` — the insert fails on a duplicate,
  so a second instance racing the first sends nothing rather than sending
  twice.
* **Catch-up, not backfill.** A missed day still fires while the reminder is
  in its window, but nothing older is resurrected on restart.
"""

import calendar
import logging
from datetime import date, datetime, timedelta, timezone

from bson import ObjectId
from motor.motor_asyncio import AsyncIOMotorDatabase
from pymongo.errors import DuplicateKeyError

from app.services import fx_service

logger = logging.getLogger(__name__)

#: How many days before the due date to remind. Both fire independently.
REMINDER_LEAD_DAYS = (10, 5)


def _clamp_due_day(day: int, year: int, month: int) -> date:
    """The due day in a given month, clamped to months that are too short.

    A card due on the 31st is due on the 28th/30th in months that end sooner —
    the same rule issuers use, and without it February would silently skip.
    """
    last_day = calendar.monthrange(year, month)[1]
    return date(year, month, min(day, last_day))


def next_due_date(due_day: int, today: date | None = None) -> date:
    """The next occurrence of ``due_day`` on or after ``today``."""
    today = today or datetime.now(timezone.utc).date()
    this_month = _clamp_due_day(due_day, today.year, today.month)
    if this_month >= today:
        return this_month
    year = today.year + (today.month == 12)
    month = 1 if today.month == 12 else today.month + 1
    return _clamp_due_day(due_day, year, month)


def _validate(data: dict) -> dict:
    name = str(data.get("name") or "").strip()
    if not name:
        raise ValueError("Give the card a name.")

    try:
        due_day = int(data.get("dueDay"))
    except (TypeError, ValueError):
        raise ValueError("Pick the day of the month the bill is due.")
    if not 1 <= due_day <= 31:
        raise ValueError("The due day must be between 1 and 31.")

    last4 = "".join(ch for ch in str(data.get("last4") or "") if ch.isdigit())[-4:]

    amount = data.get("amount")
    if amount not in (None, ""):
        try:
            amount = round(float(amount), 2)
        except (TypeError, ValueError):
            amount = None
        if amount is not None and amount < 0:
            amount = None
    else:
        amount = None

    return {
        "name": name[:60],
        "last4": last4,
        "dueDay": due_day,
        "amount": amount,
        "currency": fx_service.normalize_currency(data.get("currency")),
        "remindersEnabled": bool(data.get("remindersEnabled", True)),
    }


def serialize(card: dict) -> dict:
    due = next_due_date(int(card.get("dueDay", 1)))
    return {
        "id": str(card["_id"]),
        "name": card.get("name", ""),
        "last4": card.get("last4", ""),
        "dueDay": int(card.get("dueDay", 1)),
        "amount": card.get("amount"),
        "currency": card.get("currency", ""),
        "remindersEnabled": bool(card.get("remindersEnabled", True)),
        "nextDueDate": due.isoformat(),
        "daysUntilDue": (due - datetime.now(timezone.utc).date()).days,
    }


async def list_cards(db: AsyncIOMotorDatabase, user_id: str) -> list[dict]:
    cards = (
        await db.credit_cards.find({"userId": ObjectId(user_id)})
        .sort("name", 1)
        .to_list(length=100)
    )
    return [serialize(c) for c in cards]


async def create_card(
    db: AsyncIOMotorDatabase, user_id: str, data: dict
) -> dict:
    doc = _validate(data)
    now = datetime.now(timezone.utc)
    doc.update(
        {"userId": ObjectId(user_id), "createdAt": now, "updatedAt": now}
    )
    result = await db.credit_cards.insert_one(doc)
    doc["_id"] = result.inserted_id
    return serialize(doc)


async def update_card(
    db: AsyncIOMotorDatabase, user_id: str, card_id: str, data: dict
) -> dict | None:
    doc = _validate(data)
    doc["updatedAt"] = datetime.now(timezone.utc)
    # Scoped by userId, so a known card id from another account matches nothing.
    updated = await db.credit_cards.find_one_and_update(
        {"_id": ObjectId(card_id), "userId": ObjectId(user_id)},
        {"$set": doc},
        return_document=True,
    )
    return serialize(updated) if updated else None


async def delete_card(
    db: AsyncIOMotorDatabase, user_id: str, card_id: str
) -> bool:
    result = await db.credit_cards.delete_one(
        {"_id": ObjectId(card_id), "userId": ObjectId(user_id)}
    )
    if result.deleted_count:
        await db.card_reminder_log.delete_many({"cardId": ObjectId(card_id)})
    return bool(result.deleted_count)


async def _claim_reminder(
    db: AsyncIOMotorDatabase, card_id: ObjectId, due: date, lead: int
) -> bool:
    """Reserve one (card, due date, lead) send. False if already taken.

    The unique index does the arbitration, so this is safe across instances —
    the loser of a race gets DuplicateKeyError and simply doesn't send.
    """
    try:
        await db.card_reminder_log.insert_one(
            {
                "cardId": card_id,
                "dueDate": due.isoformat(),
                "leadDays": lead,
                "sentAt": datetime.now(timezone.utc),
            }
        )
        return True
    except DuplicateKeyError:
        return False


async def send_due_reminders(
    db: AsyncIOMotorDatabase, today: date | None = None
) -> int:
    """Send every reminder that is due today. Returns how many were sent."""
    from app.services import email_service

    today = today or datetime.now(timezone.utc).date()
    sent = 0

    cards = await db.credit_cards.find(
        {"remindersEnabled": {"$ne": False}}
    ).to_list(length=None)
    if not cards:
        return 0

    owners = await db.users.find(
        {"_id": {"$in": [c["userId"] for c in cards]}},
        {"email": 1, "name": 1, "isDeleted": 1},
    ).to_list(length=None)
    owner_map = {str(o["_id"]): o for o in owners}

    for card in cards:
        owner = owner_map.get(str(card.get("userId")))
        if not owner or owner.get("isDeleted") or not owner.get("email"):
            continue

        due = next_due_date(int(card.get("dueDay", 1)), today)
        days_until = (due - today).days
        if days_until not in REMINDER_LEAD_DAYS:
            continue
        if not await _claim_reminder(db, card["_id"], due, days_until):
            continue

        email_service.send_in_background(
            email_service.send_card_reminder_email(
                to_email=owner["email"],
                to_name=owner.get("name", ""),
                card_name=card.get("name", ""),
                due_date=due.strftime("%d %b %Y"),
                days_until=days_until,
                amount=card.get("amount"),
                currency=card.get("currency"),
            )
        )
        sent += 1

    if sent:
        logger.info("Queued %s credit-card reminder(s)", sent)
    return sent


async def purge_old_reminder_logs(db: AsyncIOMotorDatabase) -> None:
    """Drop send records older than a couple of billing cycles.

    They exist only to stop a repeat send inside the reminder window; keeping
    them forever would grow a collection nothing ever reads.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(days=90)
    await db.card_reminder_log.delete_many({"sentAt": {"$lt": cutoff}})
