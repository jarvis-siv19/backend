from datetime import datetime, timezone
from bson import ObjectId
from fastapi import HTTPException
from motor.motor_asyncio import AsyncIOMotorDatabase
from app.config import settings
from app.services import audit_service, fx_service
from app.services.user_service import serialize_doc, get_user_name_by_id


async def get_user_events(db: AsyncIOMotorDatabase, user_id: str) -> dict:
    """Get all events for a user with computed dues."""
    pipeline = [
        {"$match": {"users": ObjectId(user_id)}},
        {
            "$addFields": {
                "expenses": {"$ifNull": ["$expenses", []]},
                "users": {"$ifNull": ["$users", []]},
            }
        },
        {
            "$lookup": {
                "from": "expense",
                "let": {"expensesIds": "$expenses"},
                "pipeline": [
                    {"$match": {"$expr": {"$in": ["$_id", "$$expensesIds"]}}},
                    {"$project": {"_id": 1, "payShares": 1, "shares": 1}},
                ],
                "as": "eventExpenses",
            }
        },
        {
            # Collection is `users` (plural). Looking up "user" always matched
            # nothing, so every member resolved to "Unknown" in the event list.
            "$lookup": {
                "from": "users",
                "let": {"userIds": "$users"},
                "pipeline": [
                    {"$match": {"$expr": {"$in": ["$_id", "$$userIds"]}}},
                    {"$project": {"_id": 1, "name": 1, "email": 1}},
                ],
                "as": "eventUsers",
            }
        },
        {
            "$group": {
                "_id": "$_id",
                "eventName": {"$first": "$eventName"},
                "description": {"$first": "$description"},
                "currency": {"$first": "$currency"},
                "createdAt": {"$first": "$createdAt"},
                "createdBy": {"$first": "$createdBy"},
                "users": {"$first": "$users"},
                "expenses": {"$first": "$eventExpenses"},
                "eventUsers": {"$push": "$eventUsers"},
            }
        },
    ]

    events = await db.event.aggregate(pipeline).to_list(length=None)
    events_list = []
    overall_owe_amount = 0
    owing_person = "user"
    user_map = {}
    same_name_dict = {}
    same_name_list = set()

    # Build user map
    for event in events:
        for user_list in event.get("eventUsers", []):
            for user in user_list:
                user_map[str(user["_id"])] = user

    user_name = user_map.get(str(user_id), {}).get("name", "You")

    # Overall totals are shown in the viewer's default currency; per-event dues
    # stay in the event's own currency.
    viewer = await db.users.find_one(
        {"_id": ObjectId(user_id)}, {"defaultCurrency": 1, "fxRates": 1}
    )
    viewer_currency = fx_service.normalize_currency(
        (viewer or {}).get("defaultCurrency")
    )
    event_currencies = {
        fx_service.normalize_currency(e.get("currency")) for e in events
    }
    to_viewer_rates = await fx_service.get_rates_to(
        viewer_currency,
        event_currencies,
        db=db,
        tables=[((viewer or {}).get("fxRates"), viewer_currency)],
    )

    for event in events:
        event_currency = fx_service.normalize_currency(event.get("currency"))
        event_rate = to_viewer_rates.get(event_currency, 1.0)
        event_dict = {
            "id": str(event["_id"]),
            "users": [str(u) for u in event.get("users", [])],
            "eventName": event.get("eventName", ""),
            "description": event.get("description", ""),
            "currency": event_currency,
            "createdAt": str(event.get("createdAt", "")),
            "createdBy": str(event.get("createdBy", "")),
            "expenses": [str(exp["_id"]) for exp in event.get("expenses", [])],
            "dues": {
                "userName": user_name,
                "currency": event_currency,
                "inDebtTo": [],
                "isOwed": [],
                "totalDebt": 0,
                "totalOwed": 0,
            },
        }

        due_dict = {}
        for expense in event.get("expenses", []):
            shares = expense.get("shares", [])
            pay_shares = expense.get("payShares", [])
            total_paid = (
                sum(float(ps["amount"]) for ps in pay_shares) if pay_shares else 0
            )

            for share in shares:
                participant_id = str(share["userId"])
                share_amount = float(share["amount"])

                name = user_map.get(participant_id, {}).get("name", "")
                email = user_map.get(participant_id, {}).get("email", "")
                if name:
                    if name not in same_name_dict:
                        same_name_dict[name] = email
                    elif email != same_name_dict[name]:
                        same_name_list.add(name)

                for pay_share in pay_shares:
                    payer_id = str(pay_share["userId"])
                    payer_amount = float(pay_share["amount"])
                    if total_paid == 0:
                        continue

                    proportional_share = share_amount * (payer_amount / total_paid)

                    if participant_id == payer_id:
                        continue

                    if participant_id == str(user_id):
                        due_dict[payer_id] = (
                            due_dict.get(payer_id, 0.0) + proportional_share
                        )
                    elif payer_id == str(user_id):
                        due_dict[participant_id] = (
                            due_dict.get(participant_id, 0.0) - proportional_share
                        )

        for due_id, amount in due_dict.items():
            user_data = user_map.get(due_id, {})
            name = user_data.get("name", "Unknown")
            if name in same_name_list:
                name += f" ( {user_data.get('email', '')} )"
            abs_amount = float(abs(amount))

            if amount < 0:
                event_dict["dues"]["isOwed"].append(
                    {"id": str(due_id), "name": name, "amount": abs_amount}
                )
                event_dict["dues"]["totalOwed"] += abs_amount
                overall_owe_amount += amount * event_rate
            else:
                event_dict["dues"]["inDebtTo"].append(
                    {"id": str(due_id), "name": name, "amount": abs_amount}
                )
                event_dict["dues"]["totalDebt"] += abs_amount
                overall_owe_amount += amount * event_rate

        events_list.append(event_dict)

    if overall_owe_amount < 0:
        owing_person = "friend"
        overall_owe_amount = abs(overall_owe_amount)

    return {
        "overallOweAmount": round(float(overall_owe_amount), 2),
        "owingPerson": owing_person,
        "currency": viewer_currency,
        "events": events_list,
    }


async def is_event_member(
    db: AsyncIOMotorDatabase, event_id: str, user_id: str
) -> bool:
    """True if the user belongs to the event. Guards event reads/writes."""
    try:
        event = await db.event.find_one({"_id": ObjectId(event_id)}, {"users": 1})
        return bool(event) and ObjectId(user_id) in event.get("users", [])
    except Exception:
        return False


async def get_event_by_id(db: AsyncIOMotorDatabase, event_id: str) -> dict | None:
    """Get single event with populated user details."""
    event = await db.event.find_one({"_id": ObjectId(event_id)})
    if not event:
        return None

    user_data = []
    for user_ref in event.get("users", []):
        user = await db.users.find_one(
            {"_id": user_ref if isinstance(user_ref, ObjectId) else ObjectId(user_ref)}
        )
        if user:
            user_data.append(
                {
                    "id": str(user["_id"]),
                    "name": user.get("name", ""),
                    "email": user.get("email", ""),
                }
            )

    result = serialize_doc(event)
    result["users"] = user_data
    return result


async def save_event(
    db: AsyncIOMotorDatabase, user_id: str, request_data: dict
) -> dict:
    """Create or update an event."""
    now = datetime.now(timezone.utc)

    if "id" in request_data and request_data["id"]:
        # Update existing event
        event_id = request_data["id"]
        event = await db.event.find_one({"_id": ObjectId(event_id)})
        if not event:
            raise ValueError("Event not found")

        # Only members may edit an event.
        if ObjectId(user_id) not in event.get("users", []):
            raise HTTPException(
                status_code=403, detail="You are not a member of this event"
            )

        # Resolve users - could be ObjectId strings or email addresses. An
        # absent `users` key means "leave the membership alone", not "empty it".
        if "users" in request_data:
            new_user_ids, invited = await _resolve_users(
                db,
                request_data["users"],
                actor_id=user_id,
                extra_allowed={str(u) for u in event.get("users", [])},
            )
        else:
            new_user_ids, invited = list(event.get("users", [])), []

        original_set = set(str(u) for u in event.get("users", []))
        modified_set = set(str(u) for u in new_user_ids)
        removed_elements = original_set - modified_set

        if str(event.get("createdBy")) in removed_elements:
            raise ValueError("Cannot remove event creator")

        # Check removed users aren't in any expenses
        for expense_ref in event.get("expenses", []):
            expense = await db.expense.find_one({"_id": expense_ref if isinstance(expense_ref, ObjectId) else ObjectId(expense_ref)})
            if expense:
                for ps in expense.get("payShares", []):
                    if str(ps["userId"]) in removed_elements:
                        raise ValueError("Cannot remove user who has paid for expenses")
                for s in expense.get("shares", []):
                    if str(s["userId"]) in removed_elements:
                        raise ValueError("Cannot remove user who is in expense shares")

        # `.get(key, default)` returns None when the key is present *and* null,
        # which a partial client update sends — that wiped the event's name.
        update_data = {
            "eventName": request_data.get("eventName") or event.get("eventName"),
            "description": request_data.get("description")
            or event.get("description", ""),
            "users": new_user_ids,
            "updatedBy": ObjectId(user_id),
            "updatedAt": now,
        }

        # Currency can only change while the event has no expenses — otherwise
        # stored converted amounts would no longer match the event currency.
        new_currency = request_data.get("currency")
        if new_currency:
            new_currency = fx_service.normalize_currency(new_currency)
            current_currency = fx_service.normalize_currency(event.get("currency"))
            if new_currency != current_currency:
                if event.get("expenses"):
                    raise ValueError(
                        "Cannot change event currency after expenses are added"
                    )
                update_data["currency"] = new_currency

        # The group's own FX rates. Unlike the currency these stay editable
        # forever: they only affect expenses entered *after* the change, since
        # each expense stores the rate it was converted at.
        if request_data.get("fxRates") is not None:
            update_data["fxRates"] = fx_service.clean_rate_table(
                request_data["fxRates"]
            )

        if request_data.get("simplifyDebts") is not None:
            update_data["simplifyDebts"] = bool(request_data["simplifyDebts"])

        await db.event.update_one({"_id": ObjectId(event_id)}, {"$set": update_data})

        updated = await db.event.find_one({"_id": ObjectId(event_id)})
        await audit_service.record(
            db,
            entity="event",
            entity_id=event_id,
            action="update",
            actor_id=user_id,
            # Removed members see the change that removed them.
            audience=list(event.get("users", []))
            + list((updated or {}).get("users", [])),
            summary=f"the group {update_data['eventName']}",
            before=audit_service.snapshot(event),
            after=audit_service.snapshot(updated),
        )

        # Auto-add friends
        for uid in new_user_ids:
            if str(uid) != user_id:
                await _auto_add_friend(db, user_id, str(uid))

        await _send_member_invites(db, user_id, invited)
        return await get_event_by_id(db, event_id)
    else:
        # Create new event
        user_ids, invited = await _resolve_users(
            db, request_data.get("users", []), actor_id=user_id
        )

        # Ensure creator is in the list
        creator_oid = ObjectId(user_id)
        if creator_oid not in user_ids:
            user_ids.insert(0, creator_oid)

        # Event currency: explicit choice, else the creator's default currency
        currency = request_data.get("currency")
        if not currency:
            creator = await db.users.find_one(
                {"_id": creator_oid}, {"defaultCurrency": 1}
            )
            currency = (creator or {}).get("defaultCurrency")
        currency = fx_service.normalize_currency(currency)

        new_event = {
            "eventName": request_data.get("eventName") or "Untitled Event",
            "description": request_data.get("description") or "",
            "currency": currency,
            "fxRates": fx_service.clean_rate_table(request_data.get("fxRates")),
            "simplifyDebts": bool(request_data.get("simplifyDebts")),
            "users": user_ids,
            "expenses": [],
            "createdBy": creator_oid,
            "createdAt": now,
            "updatedBy": creator_oid,
            "updatedAt": now,
        }
        result = await db.event.insert_one(new_event)
        new_event["_id"] = result.inserted_id

        await audit_service.record(
            db,
            entity="event",
            entity_id=result.inserted_id,
            action="create",
            actor_id=user_id,
            audience=user_ids,
            summary=f"the group {new_event['eventName']}",
            after=audit_service.snapshot(new_event),
        )

        # Auto-add friends
        for uid in user_ids:
            if str(uid) != user_id:
                await _auto_add_friend(db, user_id, str(uid))

        await _send_member_invites(db, user_id, invited)
        return await get_event_by_id(db, str(result.inserted_id))


async def _is_phantom(db: AsyncIOMotorDatabase, oid: ObjectId) -> bool:
    """True if the id belongs to a placeholder account nobody has claimed.

    Phantoms represent people who have not registered, so adding one to an
    event cannot expose a real user's data — the Splitwise import creates them
    and then passes their ids straight into event creation.
    """
    user = await db.users.find_one(
        {"_id": oid}, {"isPhantom": 1, "firebaseUid": 1}
    )
    if not user:
        return False
    return bool(user.get("isPhantom")) or str(
        user.get("firebaseUid", "")
    ).startswith("phantom_")


async def _resolve_users(
    db: AsyncIOMotorDatabase,
    user_refs: list,
    actor_id: str,
    extra_allowed: set[str] | None = None,
) -> tuple[list, list]:
    """Resolve user references to ObjectIds.

    References may be ObjectId strings or email addresses. An email that isn't a
    registered user creates a phantom account (find-or-create) so the person is
    actually added to the event instead of being silently dropped; newly
    created phantoms are returned so the caller can email them an invite.
    Returns ``(user_ids, newly_invited_phantoms)``.

    ``actor_id`` bounds who may be added: the actor's existing friends, plus
    people who are not registered yet (a fresh phantom the actor is inviting),
    plus ``extra_allowed`` (on edit, the event's current members — they may
    have been added by a different member and need not be the actor's friends).
    An existing registered stranger is rejected — otherwise any authenticated
    user could pull an arbitrary victim into an event they control, which
    exposes the victim's email and (via _auto_add_friend) writes an unconsented
    friendship into the victim's own document.
    """
    from app.services import user_service

    actor_oid = ObjectId(actor_id)
    actor = await db.users.find_one({"_id": actor_oid}, {"friends": 1})
    allowed = {str(f) for f in (actor or {}).get("friends", [])}
    allowed.add(str(actor_oid))
    allowed |= extra_allowed or set()

    user_ids: list = []
    invited: list = []
    rejected: list = []
    for ref in user_refs:
        if isinstance(ref, dict):
            ref = ref.get("id", ref.get("_id", ""))
        ref = str(ref).strip()
        if not ref:
            continue
        try:
            oid = ObjectId(ref)
            if str(oid) not in allowed and not await _is_phantom(db, oid):
                rejected.append(ref)
                continue
            if oid not in user_ids:
                user_ids.append(oid)
            continue
        except Exception:
            pass
        # Not an ObjectId — treat as an email and find-or-create the user.
        if "@" in ref:
            phantom = await user_service.create_phantom_user(db, email=ref)
            try:
                oid = ObjectId(phantom["id"])
            except Exception:
                continue
            # A pre-existing *registered* account reached by email is only
            # allowed if the actor is already connected to them.
            if (
                not phantom.get("wasCreated")
                and str(oid) not in allowed
                and not await _is_phantom(db, oid)
            ):
                rejected.append(ref)
                continue
            if oid not in user_ids:
                user_ids.append(oid)
            if phantom.get("wasCreated"):
                invited.append(phantom)

    if rejected:
        raise HTTPException(
            status_code=403,
            detail="You can only add your friends or invite new people by email.",
        )
    return user_ids, invited


async def _send_member_invites(
    db: AsyncIOMotorDatabase, inviter_id: str, invited: list
) -> None:
    """Email newly-created phantom members an invite to join."""
    if not invited:
        return
    from app.services import email_service

    inviter = await db.users.find_one({"_id": ObjectId(inviter_id)}, {"name": 1})
    inviter_name = (inviter or {}).get("name", "")
    for phantom in invited:
        email = phantom.get("email")
        if email:
            email_service.send_in_background(
                email_service.send_invite_email(
                    to_email=email,
                    to_name=phantom.get("name", ""),
                    inviter_name=inviter_name,
                )
            )


async def _auto_add_friend(db: AsyncIOMotorDatabase, user_id: str, friend_id: str):
    """Silently add a friend if they're not already friends."""
    user_oid = ObjectId(user_id)
    friend_oid = ObjectId(friend_id)

    # Add friend to user's list
    await db.users.update_one(
        {"_id": user_oid, "friends": {"$ne": friend_oid}},
        {"$addToSet": {"friends": friend_oid}},
    )
    # Add user to friend's list
    await db.users.update_one(
        {"_id": friend_oid, "friends": {"$ne": user_oid}},
        {"$addToSet": {"friends": user_oid}},
    )


async def delete_event(
    db: AsyncIOMotorDatabase, event_id: str, user_id: str | None = None
) -> dict:
    """Delete an event and its expenses (if no unsettled dues)."""
    event = await db.event.find_one({"_id": ObjectId(event_id)})
    if not event:
        return {"message": "Event not found", "success": "false"}

    # Check for unsettled dues
    dues = await get_event_dues(db, event_id)
    if dues and "eventDues" in dues:
        for due in dues["eventDues"]:
            if due.get("creditorDetails") and len(due["creditorDetails"]) > 0:
                return {
                    "message": "Cannot delete event. There are unsettled dues.",
                    "success": "false",
                }

    # Snapshot the expenses before deleting them: undoing the group deletion
    # has to bring its history back, not just an empty group.
    expense_ids = [
        expense_ref if isinstance(expense_ref, ObjectId) else ObjectId(expense_ref)
        for expense_ref in event.get("expenses", [])
    ]
    deleted_expenses = await db.expense.find({"_id": {"$in": expense_ids}}).to_list(
        length=None
    )
    if expense_ids:
        await db.expense.delete_many({"_id": {"$in": expense_ids}})

    await db.event.delete_one({"_id": ObjectId(event_id)})

    await audit_service.record(
        db,
        entity="event",
        entity_id=event_id,
        action="delete",
        actor_id=user_id or event.get("createdBy"),
        audience=event.get("users", []),
        summary=f"the group {event.get('eventName', '')}",
        before=audit_service.snapshot(event),
        extra={"deletedExpenses": deleted_expenses},
    )
    return {"message": "Successfully deleted event", "success": "true"}


#: Balances below this are treated as zero. Splitting a bill three ways leaves
#: sub-cent residue that would otherwise show up as a "you owe ₹0.00" row and
#: block event deletion forever.
_SETTLED_EPSILON = 0.01


def gross_debts(expenses: list) -> dict[str, dict[str, float]]:
    """Raw ``{debtor: {creditor: amount}}`` implied by every expense.

    A participant owes each payer in proportion to how much of the bill that
    payer actually fronted, so a dinner two people split but one paid produces
    a single debt, and one they both paid produces two.
    """
    debts: dict[str, dict[str, float]] = {}
    for expense in expenses:
        pay_shares = expense.get("payShares", [])
        total_paid = sum(float(ps["amount"]) for ps in pay_shares)
        if total_paid == 0:
            continue
        for share in expense.get("shares", []):
            participant_id = str(share["userId"])
            share_amount = float(share["amount"])
            for pay_share in pay_shares:
                payer_id = str(pay_share["userId"])
                if participant_id == payer_id:
                    continue
                portion = share_amount * (float(pay_share["amount"]) / total_paid)
                debts.setdefault(participant_id, {}).setdefault(payer_id, 0.0)
                debts[participant_id][payer_id] += portion
    return debts


def net_pairwise(debts: dict[str, dict[str, float]]) -> dict[str, dict[str, float]]:
    """Cancel each pair's two directions against each other.

    A owing B ₹100 while B owes A ₹40 is one ₹60 debt, not two. Everything is
    computed into a fresh dict — the previous implementation rewrote
    ``amounts_owed`` while looping over it, which both corrupted the running
    totals and could raise "dictionary changed size during iteration" once a
    pair's reverse debt did not already exist.
    """
    people = set(debts) | {c for row in debts.values() for c in row}
    netted: dict[str, dict[str, float]] = {}
    for debtor in people:
        for creditor in people:
            if debtor >= creditor:  # visit each unordered pair once
                continue
            forward = debts.get(debtor, {}).get(creditor, 0.0)
            reverse = debts.get(creditor, {}).get(debtor, 0.0)
            net = forward - reverse
            if net > _SETTLED_EPSILON:
                netted.setdefault(debtor, {})[creditor] = net
            elif -net > _SETTLED_EPSILON:
                netted.setdefault(creditor, {})[debtor] = -net
    return netted


def simplify_debts(
    debts: dict[str, dict[str, float]],
) -> dict[str, dict[str, float]]:
    """Reduce the whole group to the fewest transfers that clear every balance.

    Pairwise netting still leaves A→B→C chains. This collapses each person to a
    single net balance and then repeatedly settles the largest debtor against
    the largest creditor, which is the standard greedy min-cash-flow reduction:
    every transfer zeroes at least one person, so n people need at most n-1
    transfers instead of up to n(n-1)/2.

    Totals are preserved — each person's net position is identical to before,
    only the routing changes. That is the whole trade-off: you may end up
    paying someone you never shared a bill with.
    """
    balances: dict[str, float] = {}
    for debtor, row in debts.items():
        for creditor, amount in row.items():
            balances[debtor] = balances.get(debtor, 0.0) - amount
            balances[creditor] = balances.get(creditor, 0.0) + amount

    debtors = sorted(
        ((p, -b) for p, b in balances.items() if b < -_SETTLED_EPSILON),
        key=lambda x: -x[1],
    )
    creditors = sorted(
        ((p, b) for p, b in balances.items() if b > _SETTLED_EPSILON),
        key=lambda x: -x[1],
    )

    simplified: dict[str, dict[str, float]] = {}
    i = j = 0
    while i < len(debtors) and j < len(creditors):
        debtor, owed = debtors[i]
        creditor, due = creditors[j]
        transfer = min(owed, due)
        if transfer > _SETTLED_EPSILON:
            simplified.setdefault(debtor, {})[creditor] = (
                simplified.setdefault(debtor, {}).get(creditor, 0.0) + transfer
            )
        debtors[i] = (debtor, owed - transfer)
        creditors[j] = (creditor, due - transfer)
        if debtors[i][1] <= _SETTLED_EPSILON:
            i += 1
        if creditors[j][1] <= _SETTLED_EPSILON:
            j += 1
    return simplified


async def get_event_dues(db: AsyncIOMotorDatabase, event_id: str) -> dict:
    """Calculate who owes whom within an event.

    Groups with ``simplifyDebts`` set get the minimum-transfer routing; the
    rest keep strict pairwise balances, so you only ever settle with people you
    actually shared a bill with.
    """
    event = await db.event.find_one({"_id": ObjectId(event_id)})
    if not event:
        return {"error": "Event not found"}

    expense_ids = [
        exp_ref if isinstance(exp_ref, ObjectId) else ObjectId(exp_ref)
        for exp_ref in event.get("expenses", [])
    ]
    expenses = await db.expense.find({"_id": {"$in": expense_ids}}).to_list(
        length=None
    )

    simplify = bool(event.get("simplifyDebts"))
    debts = gross_debts(expenses)
    debts = simplify_debts(debts) if simplify else net_pairwise(debts)

    # One name lookup per person, not per pair.
    people = set(debts) | {c for row in debts.values() for c in row}
    user_name_map = {p: await get_user_name_by_id(db, p) for p in people}

    event_dues = [
        {
            "id": debtor,
            "debtor": user_name_map.get(debtor, "Unknown"),
            "creditorDetails": [
                {
                    "id": creditor,
                    "name": user_name_map.get(creditor, "Unknown"),
                    "amount": round(amount, 2),
                }
                for creditor, amount in row.items()
                if amount > _SETTLED_EPSILON
            ],
        }
        for debtor, row in debts.items()
    ]

    return {
        "eventDues": event_dues,
        "currency": event.get("currency", settings.DEFAULT_CURRENCY),
        "simplifyDebts": simplify,
    }


async def get_event_dues_for_user(
    db: AsyncIOMotorDatabase, event_id: str, user_id: str
) -> dict:
    """Calculate who owes whom for a specific user within an event."""
    result = {
        "userName": "",
        "currency": settings.DEFAULT_CURRENCY,
        "simplifyDebts": False,
        "inDebtTo": [],
        "isOwed": [],
        "totalDebt": 0,
        "totalOwed": 0,
    }

    user_name = await get_user_name_by_id(db, user_id)
    result["userName"] = user_name

    event_dues_summary = await get_event_dues(db, event_id)
    result["currency"] = event_dues_summary.get(
        "currency", settings.DEFAULT_CURRENCY
    )
    result["simplifyDebts"] = bool(event_dues_summary.get("simplifyDebts"))

    if event_dues_summary and "eventDues" in event_dues_summary:
        for person_in_debt in event_dues_summary["eventDues"]:
            if person_in_debt["id"] == user_id:
                for owed_person in person_in_debt.get("creditorDetails", []):
                    result["inDebtTo"].append(owed_person)
                    result["totalDebt"] += owed_person["amount"]
            else:
                for owed_person in person_in_debt.get("creditorDetails", []):
                    if owed_person["id"] == user_id:
                        result["isOwed"].append(
                            {
                                "id": person_in_debt["id"],
                                "name": person_in_debt["debtor"],
                                "amount": owed_person["amount"],
                            }
                        )
                        result["totalOwed"] += owed_person["amount"]

    return result


async def get_event_or_friend_users(
    db: AsyncIOMotorDatabase, user_id: str, context_type: str, context_id: str
) -> list:
    """Get users related to a context (event or friend)."""
    users = []
    if context_type == "friend":
        user = await db.users.find_one({"_id": ObjectId(user_id)})
        if user:
            users.append(
                {
                    "name": user.get("name", ""),
                    "email": user.get("email", ""),
                    "id": str(user["_id"]),
                }
            )
        friend = await db.users.find_one({"_id": ObjectId(context_id)})
        if friend:
            users.append(
                {
                    "name": friend.get("name", ""),
                    "email": friend.get("email", ""),
                    "id": str(friend["_id"]),
                }
            )
    elif context_type == "event":
        event = await db.event.find_one({"_id": ObjectId(context_id)})
        if event:
            for user_ref in event.get("users", []):
                uid = user_ref if isinstance(user_ref, ObjectId) else ObjectId(user_ref)
                user = await db.users.find_one({"_id": uid})
                if user:
                    users.append(
                        {
                            "name": user.get("name", ""),
                            "email": user.get("email", ""),
                            "id": str(user["_id"]),
                        }
                    )
    return users
