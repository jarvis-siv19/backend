from datetime import datetime, timezone

from bson import ObjectId
from fastapi import APIRouter, Depends
from motor.motor_asyncio import AsyncIOMotorDatabase

from app.auth import get_current_user
from app.database import get_db
from app.services import event_service, fx_service, user_service

router = APIRouter()


@router.get("/fx/currencies")
async def list_currencies():
    """Supported currencies with symbols (no auth: static reference data)."""
    return {
        "default": fx_service.normalize_currency(None),
        "currencies": [
            {"code": code, **meta}
            for code, meta in fx_service.SUPPORTED_CURRENCIES.items()
        ],
    }


@router.get("/fx/rate")
async def get_rate(
    from_currency: str,
    to_currency: str,
    date: str | None = None,
    event_id: str | None = None,
    current_user: dict = Depends(get_current_user),
    db: AsyncIOMotorDatabase = Depends(get_db),
):
    """Conversion rate between two currencies, optionally on a past date.

    Resolves against the caller's own rate table (and the group's, when
    ``event_id`` is given) so the expense form can prefill the rate they
    already told us about instead of silently defaulting to 1.0. ``source``
    says where the number came from, so the UI can prompt for one when the
    answer is a guess.
    """
    user_id = await user_service.get_user_internal_id(db, current_user["uid"])

    event = None
    if event_id and user_id and await event_service.is_event_member(
        db, event_id, user_id
    ):
        event = await db.event.find_one(
            {"_id": ObjectId(event_id)}, {"fxRates": 1, "currency": 1}
        )

    # Same resolution the expense write path uses, so the rate previewed here
    # is exactly the one that would be recorded.
    tables = await fx_service.context_rate_tables(db, user_id, event)
    rate = await fx_service.get_rate(
        from_currency, to_currency, on=date, db=db, tables=tables
    )

    source = "none"
    if fx_service.normalize_currency(from_currency) == fx_service.normalize_currency(
        to_currency
    ):
        source = "identity"
    else:
        for index, (table, table_currency) in enumerate(tables):
            if (
                fx_service.override_rate(
                    table, table_currency, from_currency, to_currency
                )
                is not None
            ):
                source = "event" if event is not None and index == 0 else "user"
                break
        else:
            if rate != 1.0:
                source = "stored"

    return {
        "from": fx_service.normalize_currency(from_currency),
        "to": fx_service.normalize_currency(to_currency),
        "date": date or datetime.now(timezone.utc).date().isoformat(),
        "rate": rate,
        "source": source,
    }
