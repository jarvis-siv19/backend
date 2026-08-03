"""Activity log with undo.

Every write that another person can see — expenses, events, settlements — is
recorded here with a full before/after snapshot of the affected document. Two
things fall out of that:

* **Visibility.** Anyone the change affected can see who did it and when, so a
  balance never silently moves. This is the point: in a shared ledger, an edit
  by someone else is indistinguishable from a mistake unless it is attributed.
* **Undo.** Because the snapshot is the whole document, undo is a restore
  rather than a replay of inverse operations. That matters for deletes, which
  have no inverse — the expense and its link back to its event are simply put
  back exactly as they were.

Undo is guarded three ways. An entry can only be undone once (an atomic
find-one-and-update claims it, so two people racing produce one undo). It can
only be undone by someone the entry was visible to. And it is refused if the
document moved on afterwards — restoring a stale snapshot would silently
discard whoever edited it in between, which is the exact failure this feature
exists to prevent. Undoing is itself logged, and that entry is not undoable:
redo is out of scope, and an undo/redo chain over shared money is a worse
problem than re-entering the expense.
"""

import logging
from datetime import datetime, timezone

from bson import ObjectId
from fastapi import HTTPException
from motor.motor_asyncio import AsyncIOMotorDatabase


logger = logging.getLogger(__name__)

#: Entries older than this can be read but not undone. An undo rewrites
#: balances other people may already have acted on (paid up, settled), so the
#: window is deliberately short.
UNDO_WINDOW_SECONDS = 7 * 24 * 60 * 60

ACTION_LABELS = {
    "create": "added",
    "update": "edited",
    "delete": "deleted",
    "settle": "settled up",
    "undo": "undid",
}


def _oid(value) -> ObjectId | None:
    if isinstance(value, ObjectId):
        return value
    try:
        return ObjectId(str(value))
    except Exception:
        return None


def _oid_set(values) -> list[ObjectId]:
    seen: list[ObjectId] = []
    for value in values or []:
        oid = _oid(value)
        if oid is not None and oid not in seen:
            seen.append(oid)
    return seen


def expense_audience(expense: dict, event: dict | None = None) -> list[ObjectId]:
    """Everyone entitled to see a change to this expense.

    Participants and the creator always; every member of the event when it
    belongs to one, since a group expense moves the whole group's balances even
    for members who are not on the split.
    """
    audience = [expense.get("createdBy")]
    for entry in expense.get("shares", []) + expense.get("payShares", []):
        audience.append(entry.get("userId"))
    if event is not None:
        audience.extend(event.get("users", []))
    return _oid_set(audience)


async def record(
    db: AsyncIOMotorDatabase,
    *,
    entity: str,
    entity_id,
    action: str,
    actor_id,
    audience,
    summary: str,
    before: dict | None = None,
    after: dict | None = None,
    undoable: bool = True,
    extra: dict | None = None,
) -> ObjectId | None:
    """Append an entry. Never raises — logging must not fail a write.

    An expense that saved but whose audit entry did not is a gap in the
    history; an expense that failed to save *because* of the audit entry is a
    lost expense. The first is strictly better.
    """
    try:
        doc = {
            "entity": entity,
            "entityId": _oid(entity_id) or str(entity_id),
            "action": action,
            "actorId": _oid(actor_id),
            "visibleTo": _oid_set(audience),
            "summary": summary,
            "before": before,
            "after": after,
            "undoable": undoable,
            "undoneAt": None,
            "undoneBy": None,
            "createdAt": datetime.now(timezone.utc),
        }
        if extra:
            doc.update(extra)
        result = await db.audit_log.insert_one(doc)
        return result.inserted_id
    except Exception as e:
        logger.error("Failed to write audit entry (%s %s): %s", action, entity, e)
        return None


def _within_undo_window(entry: dict) -> bool:
    created = entry.get("createdAt")
    if not isinstance(created, datetime):
        return False
    if created.tzinfo is None:
        created = created.replace(tzinfo=timezone.utc)
    return (
        datetime.now(timezone.utc) - created
    ).total_seconds() <= UNDO_WINDOW_SECONDS


def can_undo(entry: dict) -> bool:
    if not entry.get("undoable") or entry.get("undoneAt"):
        return False
    if not _within_undo_window(entry):
        return False
    # Reversing a create or a settle-up means removing what it made, so those
    # need no prior image. Everything else is a restore and does.
    if entry.get("action") in ("create", "settle"):
        return True
    return entry.get("before") is not None


async def get_activity(
    db: AsyncIOMotorDatabase, user_id: str, limit: int = 50, skip: int = 0
) -> list[dict]:
    """The caller's activity feed, newest first."""
    oid = _oid(user_id)
    if oid is None:
        return []
    limit = max(1, min(int(limit or 50), 200))
    entries = (
        await db.audit_log.find({"visibleTo": oid})
        .sort("createdAt", -1)
        .skip(max(0, int(skip or 0)))
        .limit(limit)
        .to_list(length=limit)
    )

    actor_ids = {e["actorId"] for e in entries if e.get("actorId")}
    actors = await db.users.find(
        {"_id": {"$in": list(actor_ids)}}, {"name": 1}
    ).to_list(length=None)
    names = {str(a["_id"]): a.get("name", "Someone") for a in actors}

    feed = []
    for entry in entries:
        actor_id = str(entry.get("actorId", ""))
        is_self = actor_id == str(user_id)
        feed.append(
            {
                "id": str(entry["_id"]),
                "entity": entry.get("entity", ""),
                "entityId": str(entry.get("entityId", "")),
                "action": entry.get("action", ""),
                "actionLabel": ACTION_LABELS.get(entry.get("action", ""), ""),
                "actorId": actor_id,
                "actorName": "You" if is_self else names.get(actor_id, "Someone"),
                "isYou": is_self,
                "summary": entry.get("summary", ""),
                "createdAt": entry["createdAt"].isoformat()
                if isinstance(entry.get("createdAt"), datetime)
                else str(entry.get("createdAt", "")),
                "canUndo": can_undo(entry),
                "undone": bool(entry.get("undoneAt")),
            }
        )
    return feed


async def _restore_expense(db: AsyncIOMotorDatabase, snapshot: dict) -> None:
    """Put a deleted expense back, including its link from the event."""
    await db.expense.insert_one(snapshot)
    event_id = snapshot.get("eventId")
    if event_id:
        await db.event.update_one(
            {"_id": event_id}, {"$addToSet": {"expenses": snapshot["_id"]}}
        )


async def _remove_expense(db: AsyncIOMotorDatabase, expense_id, event_id) -> None:
    await db.expense.delete_one({"_id": expense_id})
    if event_id:
        await db.event.update_one(
            {"_id": event_id}, {"$pull": {"expenses": expense_id}}
        )


async def _assert_unchanged(
    db: AsyncIOMotorDatabase, collection, entry: dict
) -> dict | None:
    """Refuse the undo if someone edited the document after this entry.

    Restoring `before` on top of a newer edit would erase that edit without a
    trace, which is precisely the surprise the activity log exists to stop.
    """
    current = await collection.find_one({"_id": entry["entityId"]})
    after = entry.get("after") or {}
    expected = after.get("updatedAt")
    if current is not None and expected is not None:
        actual = current.get("updatedAt")
        if actual is not None and actual != expected:
            raise HTTPException(
                status_code=409,
                detail="This was changed again after that action. Refresh and review it before undoing.",
            )
    return current


async def undo(db: AsyncIOMotorDatabase, user_id: str, log_id: str) -> dict:
    """Reverse a logged action, if the caller is allowed to and it still can be."""
    actor_oid = _oid(user_id)
    entry_oid = _oid(log_id)
    if actor_oid is None or entry_oid is None:
        raise HTTPException(status_code=404, detail="Activity entry not found")

    entry = await db.audit_log.find_one({"_id": entry_oid})
    if not entry or actor_oid not in entry.get("visibleTo", []):
        # Same response for "no such entry" and "not yours": otherwise this
        # endpoint enumerates other people's activity ids.
        raise HTTPException(status_code=404, detail="Activity entry not found")
    if entry.get("undoneAt"):
        raise HTTPException(status_code=400, detail="This was already undone.")
    if not can_undo(entry):
        raise HTTPException(status_code=400, detail="This action can no longer be undone.")

    entity = entry.get("entity")
    action = entry.get("action")
    before = entry.get("before")
    after = entry.get("after") or {}

    if entity == "expense":
        if action == "create":
            await _assert_unchanged(db, db.expense, entry)
            await _remove_expense(db, entry["entityId"], after.get("eventId"))
            summary = f"Removed the expense they added: {after.get('expenseName', '')}"
        elif action == "update":
            await _assert_unchanged(db, db.expense, entry)
            if before is None:
                raise HTTPException(status_code=400, detail="Nothing to restore.")
            await db.expense.replace_one({"_id": entry["entityId"]}, before)
            summary = f"Restored the previous version of {before.get('expenseName', '')}"
        elif action == "delete":
            if before is None:
                raise HTTPException(status_code=400, detail="Nothing to restore.")
            if await db.expense.find_one({"_id": entry["entityId"]}):
                raise HTTPException(
                    status_code=409, detail="That expense already exists again."
                )
            await _restore_expense(db, before)
            summary = f"Restored the deleted expense: {before.get('expenseName', '')}"
        else:
            raise HTTPException(status_code=400, detail="This action cannot be undone.")

    elif entity == "event":
        if action == "create":
            event = await db.event.find_one({"_id": entry["entityId"]})
            if event and event.get("expenses"):
                raise HTTPException(
                    status_code=409,
                    detail="This group now has expenses, so it can't be removed by undo.",
                )
            await db.event.delete_one({"_id": entry["entityId"]})
            summary = f"Removed the group they created: {after.get('eventName', '')}"
        elif action == "update":
            await _assert_unchanged(db, db.event, entry)
            if before is None:
                raise HTTPException(status_code=400, detail="Nothing to restore.")
            await db.event.replace_one({"_id": entry["entityId"]}, before)
            summary = f"Restored the previous settings of {before.get('eventName', '')}"
        elif action == "delete":
            if before is None:
                raise HTTPException(status_code=400, detail="Nothing to restore.")
            if await db.event.find_one({"_id": entry["entityId"]}):
                raise HTTPException(
                    status_code=409, detail="That group already exists again."
                )
            # Deleting an event cascades to its expenses, so undoing it has to
            # restore them too or the group comes back with its history gone.
            for snapshot in entry.get("deletedExpenses") or []:
                if not await db.expense.find_one({"_id": snapshot["_id"]}):
                    await db.expense.insert_one(snapshot)
            await db.event.insert_one(before)
            summary = f"Restored the deleted group: {before.get('eventName', '')}"
        else:
            raise HTTPException(status_code=400, detail="This action cannot be undone.")

    elif entity == "settlement":
        # A settle-up is stored as one or more `settle` expenses; undoing it
        # removes exactly those, which reopens the balance it closed.
        expense_ids = [_oid(i) for i in after.get("expenseIds", [])]
        expense_ids = [i for i in expense_ids if i]
        for expense_id in expense_ids:
            expense = await db.expense.find_one({"_id": expense_id}, {"eventId": 1})
            if expense:
                await _remove_expense(db, expense_id, expense.get("eventId"))
        await db.settlement_request.update_one(
            {"_id": entry["entityId"]},
            {"$set": {"status": "undone", "updatedAt": datetime.now(timezone.utc)}},
        )
        summary = "Reopened a settle-up"

    else:
        raise HTTPException(status_code=400, detail="This action cannot be undone.")

    # Claim the entry atomically: two people hitting undo at once must produce
    # one undo, and the loser must be told rather than silently no-op.
    claimed = await db.audit_log.find_one_and_update(
        {"_id": entry_oid, "undoneAt": None},
        {
            "$set": {
                "undoneAt": datetime.now(timezone.utc),
                "undoneBy": actor_oid,
            }
        },
    )
    if claimed is None:
        raise HTTPException(status_code=400, detail="This was already undone.")

    await record(
        db,
        entity=entity,
        entity_id=entry["entityId"],
        action="undo",
        actor_id=actor_oid,
        audience=entry.get("visibleTo", []),
        summary=summary,
        undoable=False,
    )

    return {"success": "true", "message": "Undone", "summary": summary}


def snapshot(doc: dict | None) -> dict | None:
    """A copy safe to store as a before/after image."""
    return dict(doc) if doc else None
