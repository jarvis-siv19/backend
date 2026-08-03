"""Currency conversion service.

**Users set their own rates.** There is deliberately no mandatory live rate
feed: the real rate a person is charged includes their card's forex markup, so
a mid-market feed would be wrong anyway. Whoever enters the expense knows what
the money actually cost them, so they type that in.

Rates are resolved in this order:
1. An explicit per-expense ``fxRate`` (what this transaction actually cost).
2. Override tables passed by the caller — the event's ``fxRates`` map, then the
   user's own ``fxRates`` map. Each maps ``{currency code: rate into that
   context's currency}``; see :func:`override_rate`.
3. The local ``fx_rates`` Mongo collection (docs: ``{base, quote, rate, date}``),
   so rates can also be bulk-loaded directly into the DB.
4. The external FX API (``FX_API_URL``), if one is configured. Optional and
   unset by default.
5. Identity (1.0) — amounts pass through unconverted, keeping the app usable
   before any rate is entered.

Only step 4 is cached (in-memory per (base, quote, date) for 12 hours); user
tables are read from the documents the caller already has in hand, so editing a
rate takes effect immediately.
"""

import logging
import time
from datetime import date, datetime, timezone

import httpx
from bson import ObjectId
from motor.motor_asyncio import AsyncIOMotorDatabase

from app.config import settings

logger = logging.getLogger(__name__)

# ISO code -> display metadata. Kept in sync with the Flutter currency list.
SUPPORTED_CURRENCIES = {
    "INR": {"symbol": "₹", "name": "Indian Rupee"},
    "USD": {"symbol": "$", "name": "US Dollar"},
    "EUR": {"symbol": "€", "name": "Euro"},
    "GBP": {"symbol": "£", "name": "British Pound"},
    "JPY": {"symbol": "¥", "name": "Japanese Yen"},
    "CNY": {"symbol": "¥", "name": "Chinese Yuan"},
    "AED": {"symbol": "د.إ", "name": "UAE Dirham"},
    "SGD": {"symbol": "S$", "name": "Singapore Dollar"},
    "AUD": {"symbol": "A$", "name": "Australian Dollar"},
    "CAD": {"symbol": "C$", "name": "Canadian Dollar"},
    "CHF": {"symbol": "Fr", "name": "Swiss Franc"},
    "THB": {"symbol": "฿", "name": "Thai Baht"},
    "MYR": {"symbol": "RM", "name": "Malaysian Ringgit"},
    "LKR": {"symbol": "Rs", "name": "Sri Lankan Rupee"},
    "NPR": {"symbol": "Rs", "name": "Nepalese Rupee"},
    "BDT": {"symbol": "৳", "name": "Bangladeshi Taka"},
    "IDR": {"symbol": "Rp", "name": "Indonesian Rupiah"},
    "VND": {"symbol": "₫", "name": "Vietnamese Dong"},
    "KRW": {"symbol": "₩", "name": "South Korean Won"},
    "SAR": {"symbol": "﷼", "name": "Saudi Riyal"},
    "QAR": {"symbol": "﷼", "name": "Qatari Riyal"},
    "KWD": {"symbol": "د.ك", "name": "Kuwaiti Dinar"},
    "NZD": {"symbol": "NZ$", "name": "New Zealand Dollar"},
    "ZAR": {"symbol": "R", "name": "South African Rand"},
    "HKD": {"symbol": "HK$", "name": "Hong Kong Dollar"},
    "TWD": {"symbol": "NT$", "name": "New Taiwan Dollar"},
    "PHP": {"symbol": "₱", "name": "Philippine Peso"},
    "EGP": {"symbol": "E£", "name": "Egyptian Pound"},
    "TRY": {"symbol": "₺", "name": "Turkish Lira"},
    "BRL": {"symbol": "R$", "name": "Brazilian Real"},
    "MXN": {"symbol": "Mex$", "name": "Mexican Peso"},
}

_CACHE_TTL_SECONDS = 12 * 60 * 60
# (base, quote, date-iso) -> (rate, fetched_at_monotonic)
_rate_cache: dict[tuple[str, str, str], tuple[float, float]] = {}
_warned_identity = False


def normalize_currency(code: str | None) -> str:
    """Uppercase and validate a currency code, falling back to the app default."""
    code = (code or "").strip().upper()
    if code in SUPPORTED_CURRENCIES:
        return code
    return settings.DEFAULT_CURRENCY


def _date_key(on: date | datetime | str | None) -> str:
    if on is None:
        return datetime.now(timezone.utc).date().isoformat()
    if isinstance(on, datetime):
        return on.date().isoformat()
    if isinstance(on, date):
        return on.isoformat()
    # String: keep just the date portion of an ISO timestamp
    return str(on)[:10]


async def _fetch_rate_from_api(base: str, quote: str, date_key: str) -> float | None:
    """Fetch a rate from the external FX endpoint.

    Expected contract (flexible on purpose until the real endpoint lands):
    GET {FX_API_URL}/rate?from=USD&to=INR&date=2026-07-15
    Accepted responses: {"rate": 83.2} | {"rates": {"INR": 83.2}} | 83.2
    """
    url = f"{settings.FX_API_URL.rstrip('/')}/rate"
    params = {"from": base, "to": quote, "date": date_key}
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(url, params=params)
            resp.raise_for_status()
            data = resp.json()
        if isinstance(data, (int, float)):
            return float(data)
        if isinstance(data, dict):
            if "rate" in data:
                return float(data["rate"])
            rates = data.get("rates", {})
            if quote in rates:
                return float(rates[quote])
    except Exception as e:
        logger.warning("FX API lookup failed for %s->%s@%s: %s", base, quote, date_key, e)
    return None


async def _fetch_rate_from_db(
    db: AsyncIOMotorDatabase | None, base: str, quote: str, date_key: str
) -> float | None:
    """Latest stored rate on or before the requested date, direct or inverse."""
    if db is None:
        return None
    try:
        doc = await db.fx_rates.find_one(
            {"base": base, "quote": quote, "date": {"$lte": date_key}},
            sort=[("date", -1)],
        )
        if doc and doc.get("rate"):
            return float(doc["rate"])
        inverse = await db.fx_rates.find_one(
            {"base": quote, "quote": base, "date": {"$lte": date_key}},
            sort=[("date", -1)],
        )
        if inverse and inverse.get("rate"):
            return 1.0 / float(inverse["rate"])
    except Exception as e:
        logger.warning("fx_rates DB lookup failed for %s->%s: %s", base, quote, e)
    return None


def clean_rate_table(raw) -> dict[str, float]:
    """Validate a user-supplied ``{currency: rate}`` table.

    Unknown codes and non-positive rates are dropped rather than rejected: the
    table is a convenience the user fills in over time, and one bad row should
    not fail the whole save.
    """
    table: dict[str, float] = {}
    if not isinstance(raw, dict):
        return table
    for code, value in raw.items():
        norm = str(code or "").strip().upper()
        if norm not in SUPPORTED_CURRENCIES:
            continue
        try:
            rate = float(value)
        except (TypeError, ValueError):
            continue
        if rate > 0:
            table[norm] = rate
    return table


def override_rate(
    table: dict | None, table_currency: str | None, base: str, quote: str
) -> float | None:
    """Resolve ``base -> quote`` from a user's rate table, or None.

    ``table`` maps a currency code to its rate *into* ``table_currency`` — e.g.
    an event priced in INR with ``{"USD": 86.4}`` means one US dollar cost this
    group 86.4 rupees. That single anchor answers three questions:

    * into the table currency (USD -> INR): the stored rate;
    * out of it (INR -> USD): its reciprocal;
    * between two entries (USD -> EUR): their ratio, since both are anchored to
      the same currency.
    """
    if not table:
        return None
    anchor = normalize_currency(table_currency)
    base = normalize_currency(base)
    quote = normalize_currency(quote)
    if base == quote:
        return 1.0

    into_anchor = table.get(base) if base != anchor else 1.0
    quote_rate = table.get(quote) if quote != anchor else 1.0
    if into_anchor is None or quote_rate is None:
        return None
    try:
        into_anchor = float(into_anchor)
        quote_rate = float(quote_rate)
    except (TypeError, ValueError):
        return None
    if into_anchor <= 0 or quote_rate <= 0:
        return None
    return into_anchor / quote_rate


#: A user-supplied rate table paired with the currency its rates are quoted in.
RateTable = tuple[dict | None, str | None]


async def get_rate(
    from_currency: str,
    to_currency: str,
    on: date | datetime | str | None = None,
    db: AsyncIOMotorDatabase | None = None,
    tables: list[RateTable] | None = None,
) -> float:
    """Conversion rate from ``from_currency`` to ``to_currency`` on a date.

    ``tables`` are consulted in order before any stored or remote rate, so the
    most specific thing the user told us wins: the event's own table, then
    their personal one.
    """
    global _warned_identity
    base = normalize_currency(from_currency)
    quote = normalize_currency(to_currency)
    if base == quote:
        return 1.0

    for table, table_currency in tables or []:
        rate = override_rate(table, table_currency, base, quote)
        if rate is not None:
            return rate

    date_key = _date_key(on)
    rate = await _fetch_rate_from_db(db, base, quote, date_key)
    if rate is not None:
        return rate

    if settings.FX_API_URL:
        cache_key = (base, quote, date_key)
        cached = _rate_cache.get(cache_key)
        if cached and (time.monotonic() - cached[1]) < _CACHE_TTL_SECONDS:
            return cached[0]
        rate = await _fetch_rate_from_api(base, quote, date_key)
        if rate is not None:
            _rate_cache[cache_key] = (rate, time.monotonic())
            return rate

    if not _warned_identity:
        logger.warning(
            "No FX rate for %s->%s (no user rate table entry, fx_rates empty, "
            "FX_API_URL unset); using identity rate 1.0. Ask the user to set a "
            "rate on the group, the expense, or their account.",
            base,
            quote,
        )
        _warned_identity = True
    return 1.0


async def convert(
    amount: float,
    from_currency: str,
    to_currency: str,
    on: date | datetime | str | None = None,
    db: AsyncIOMotorDatabase | None = None,
    tables: list[RateTable] | None = None,
) -> float:
    rate = await get_rate(from_currency, to_currency, on=on, db=db, tables=tables)
    return round(float(amount) * rate, 2)


async def get_rates_to(
    target_currency: str,
    base_currencies: set[str],
    db: AsyncIOMotorDatabase | None = None,
    tables: list[RateTable] | None = None,
) -> dict[str, float]:
    """Current rates from each base currency to the target, for read-time sums."""
    target = normalize_currency(target_currency)
    rates: dict[str, float] = {}
    for base in base_currencies:
        base_n = normalize_currency(base)
        if base_n not in rates:
            rates[base_n] = await get_rate(base_n, target, db=db, tables=tables)
    return rates


async def user_rate_table(
    db: AsyncIOMotorDatabase | None, user_id
) -> RateTable:
    """The viewer's own ``{currency: rate}`` table, quoted in their currency."""
    if db is None or not user_id:
        return (None, None)
    try:
        oid = user_id if isinstance(user_id, ObjectId) else ObjectId(str(user_id))
        user = await db.users.find_one(
            {"_id": oid}, {"fxRates": 1, "defaultCurrency": 1}
        )
    except Exception:
        return (None, None)
    if not user:
        return (None, None)
    return (user.get("fxRates"), normalize_currency(user.get("defaultCurrency")))


def event_rate_table(event: dict | None) -> RateTable:
    """A group's table, quoted in the group's currency."""
    if not event:
        return (None, None)
    return (event.get("fxRates"), normalize_currency(event.get("currency")))


async def context_rate_tables(
    db: AsyncIOMotorDatabase, user_id, event: dict | None = None
) -> list[RateTable]:
    """Tables to consult, most specific first: the group's, then the user's.

    This ordering is the FX feature's whole contract — a group's table wins
    because everyone on the trip shares the rate the group agreed on, and the
    personal table is the fallback for friend and solo expenses. It lives here
    so the rate an expense is *stored* at and the rate the form *previews* are
    resolved by the same code; when those two drifted apart the preview
    silently lied about what would be recorded.
    """
    tables: list[RateTable] = []
    if event is not None:
        tables.append(event_rate_table(event))
    tables.append(await user_rate_table(db, user_id))
    return tables
