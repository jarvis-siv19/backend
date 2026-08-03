"""User-set FX rates.

There is no live rate feed by design (see fx_service): the user tells us what
the money actually cost them, markup included. These pin the resolution order
and the arithmetic that order implies.
"""

import pytest

from app.services import fx_service


def test_override_rate_into_the_anchor_currency():
    # {"USD": 86.4} on an INR-priced group: one dollar cost 86.4 rupees.
    assert fx_service.override_rate({"USD": 86.4}, "INR", "USD", "INR") == 86.4


def test_override_rate_out_of_the_anchor_currency_is_the_reciprocal():
    rate = fx_service.override_rate({"USD": 80.0}, "INR", "INR", "USD")
    assert rate == pytest.approx(0.0125)


def test_override_rate_between_two_entries_uses_their_ratio():
    # Both anchored to INR, so USD->EUR is 86.4/90.0 without any extra input.
    rate = fx_service.override_rate(
        {"USD": 86.4, "EUR": 90.0}, "INR", "USD", "EUR"
    )
    assert rate == pytest.approx(86.4 / 90.0)


def test_override_rate_returns_none_when_the_pair_is_not_covered():
    assert fx_service.override_rate({"USD": 86.4}, "INR", "GBP", "JPY") is None
    assert fx_service.override_rate(None, "INR", "USD", "INR") is None


def test_clean_rate_table_drops_unusable_rows_but_keeps_the_rest():
    table = fx_service.clean_rate_table(
        {"usd": "86.4", "XYZ": 5, "EUR": 0, "GBP": "nonsense", "JPY": 0.55}
    )
    assert table == {"USD": 86.4, "JPY": 0.55}


async def test_get_rate_prefers_the_first_table_given():
    # Group table beats the personal one: the trip agreed on a rate.
    rate = await fx_service.get_rate(
        "USD", "INR", tables=[({"USD": 86.0}, "INR"), ({"USD": 80.0}, "INR")]
    )
    assert rate == 86.0


async def test_get_rate_falls_through_to_the_next_table():
    rate = await fx_service.get_rate(
        "EUR", "INR", tables=[({"USD": 86.0}, "INR"), ({"EUR": 92.0}, "INR")]
    )
    assert rate == 92.0


async def test_get_rate_without_any_source_is_identity():
    assert await fx_service.get_rate("USD", "INR") == 1.0


def test_expense_level_rate_beats_every_table(client):
    """The rate typed on the expense is what that transaction actually cost."""
    resp = client.post(
        "/db/expense",
        json={
            "expenseName": "Duty free",
            "amount": 100.0,
            "category": "Shopping",
            "currency": "USD",
            "fxRate": 87.5,
            "type": "normal",
            "date": "2026-07-20T00:00:00Z",
        },
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data["fxRate"] == 87.5
    assert data["originalAmount"] == 100.0
    # Stored in the base (account) currency at the rate the user gave.
    assert data["amount"] == 8750.0
    assert data["currency"] == "USD"
    assert data["baseCurrency"] == "INR"


def test_personal_rate_table_converts_a_friend_expense(client):
    """A one-to-one expense has no group, so the personal table is the only
    thing that can convert it — the case that silently used 1.0 before."""
    client.put("/db/user/account", json={"fxRates": {"USD": 84.0}})
    account = client.get("/db/user/account").json()
    assert account["fxRates"] == {"USD": 84.0}

    resp = client.post(
        "/db/expense",
        json={
            "expenseName": "Airport taxi",
            "amount": 20.0,
            "category": "Transport",
            "currency": "USD",
            "type": "normal",
            "date": "2026-07-21T00:00:00Z",
        },
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data["fxRate"] == 84.0
    assert data["amount"] == 1680.0


def test_group_rate_table_applies_to_group_expenses(client):
    uid = client.get("/db/user/account").json()["id"]
    event = client.post(
        "/db/event",
        json={
            "eventName": "Tokyo",
            "users": [uid],
            "currency": "JPY",
            "fxRates": {"INR": 1.8},
        },
    ).json()

    resp = client.post(
        "/db/expense",
        json={
            "expenseName": "Ramen",
            "amount": 500.0,
            "category": "Food",
            "currency": "INR",
            "type": "group",
            "eventId": event["id"],
            "shares": [{"userId": uid, "amount": 500.0}],
            "payShares": [{"userId": uid, "amount": 500.0}],
            "date": "2026-07-22T00:00:00Z",
        },
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data["fxRate"] == 1.8
    assert data["amount"] == 900.0
    assert data["baseCurrency"] == "JPY"


def test_fx_rate_endpoint_reports_where_the_number_came_from(client):
    client.put("/db/user/account", json={"fxRates": {"GBP": 108.0}})

    known = client.get(
        "/db/fx/rate", params={"from_currency": "GBP", "to_currency": "INR"}
    ).json()
    assert known["rate"] == 108.0
    assert known["source"] == "user"

    # Nothing configured for this pair — the UI uses `source` to ask for one
    # rather than quietly converting at 1.0.
    unknown = client.get(
        "/db/fx/rate", params={"from_currency": "BRL", "to_currency": "INR"}
    ).json()
    assert unknown["rate"] == 1.0
    assert unknown["source"] == "none"
