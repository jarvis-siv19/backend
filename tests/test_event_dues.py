"""Event dues: pairwise netting and debt simplification.

The netting pass used to rewrite its accumulator while iterating over it, which
both corrupted running totals and could raise "dictionary changed size during
iteration". These pin the arithmetic directly against the pure functions, so a
regression shows up as a wrong number rather than a wrong screen.
"""

import pytest

from app.services.event_service import (
    gross_debts,
    net_pairwise,
    simplify_debts,
)

A, B, C = "aaa", "bbb", "ccc"


def _expense(shares, pay_shares):
    return {
        "shares": [{"userId": u, "amount": amt} for u, amt in shares],
        "payShares": [{"userId": u, "amount": amt} for u, amt in pay_shares],
    }


def test_gross_debts_splits_a_bill_one_person_fronted():
    # A pays 300 for a 3-way split: B and C each owe A 100.
    debts = gross_debts([_expense([(A, 100), (B, 100), (C, 100)], [(A, 300)])])
    assert debts[B][A] == pytest.approx(100)
    assert debts[C][A] == pytest.approx(100)
    assert A not in debts  # the payer owes nobody


def test_gross_debts_splits_proportionally_across_two_payers():
    # A and B each fronted half, so C's 100 share splits 50/50 between them.
    debts = gross_debts(
        [_expense([(A, 100), (B, 100), (C, 100)], [(A, 150), (B, 150)])]
    )
    assert debts[C][A] == pytest.approx(50)
    assert debts[C][B] == pytest.approx(50)


def test_net_pairwise_cancels_the_two_directions_of_a_pair():
    netted = net_pairwise({A: {B: 100.0}, B: {A: 40.0}})
    assert netted == {A: {B: pytest.approx(60.0)}}


def test_net_pairwise_drops_a_pair_that_cancels_out():
    """Equal and opposite debts are settled, not a pair of zero-value rows —
    a stray ₹0.00 row blocks event deletion forever."""
    assert net_pairwise({A: {B: 75.0}, B: {A: 75.0}}) == {}


def test_net_pairwise_flips_direction_when_the_reverse_is_larger():
    netted = net_pairwise({A: {B: 30.0}, B: {A: 80.0}})
    assert netted == {B: {A: pytest.approx(50.0)}}


def test_net_pairwise_survives_a_one_sided_debt():
    """The old implementation created the reverse entry mid-iteration here."""
    assert net_pairwise({A: {B: 20.0}}) == {A: {B: pytest.approx(20.0)}}


def test_simplify_collapses_a_chain_into_one_transfer():
    # A owes B, B owes C the same amount: B is just a pass-through.
    simplified = simplify_debts({A: {B: 100.0}, B: {C: 100.0}})
    assert simplified == {A: {C: pytest.approx(100.0)}}


def test_simplify_preserves_every_net_position():
    debts = {A: {B: 60.0}, B: {C: 25.0}, C: {A: 10.0}}

    def net(d):
        balances = {}
        for debtor, row in d.items():
            for creditor, amount in row.items():
                balances[debtor] = balances.get(debtor, 0) - amount
                balances[creditor] = balances.get(creditor, 0) + amount
        return {k: round(v, 6) for k, v in balances.items() if abs(v) > 1e-6}

    assert net(simplify_debts(debts)) == net(debts)


def test_simplify_needs_at_most_one_transfer_fewer_than_people():
    debts = {A: {B: 50.0, C: 50.0}, B: {C: 30.0}}
    transfers = sum(len(row) for row in simplify_debts(debts).values())
    assert transfers <= 2


def test_event_dues_endpoint_reports_the_simplify_setting(client):
    uid = client.get("/db/user/account").json()["id"]
    event_id = client.post(
        "/db/event",
        json={
            "eventName": "Simplified",
            "users": [uid],
            "currency": "INR",
            "simplifyDebts": True,
        },
    ).json()["id"]

    dues = client.get(f"/db/event/{event_id}/dues").json()
    assert dues["simplifyDebts"] is True

    client.put(
        "/db/event",
        json={"id": event_id, "eventName": "Simplified", "simplifyDebts": False},
    )
    assert client.get(f"/db/event/{event_id}/dues").json()["simplifyDebts"] is False


def test_editing_an_event_without_a_name_does_not_wipe_it(client):
    """`.get(key, default)` returns None when the key is present and null, so a
    partial update used to blank the group's name."""
    uid = client.get("/db/user/account").json()["id"]
    event_id = client.post(
        "/db/event", json={"eventName": "Keep My Name", "users": [uid], "currency": "INR"}
    ).json()["id"]

    client.put("/db/event", json={"id": event_id, "users": [uid], "description": "x"})

    assert client.get(f"/db/event/{event_id}").json()["eventName"] == "Keep My Name"


def test_event_list_resolves_member_names(client):
    """The members lookup targeted a `user` collection that does not exist, so
    every name in the event list came back as "Unknown"."""
    uid = client.get("/db/user/account").json()["id"]
    client.post(
        "/db/event", json={"eventName": "Named", "users": [uid], "currency": "INR"}
    )

    events = client.get("/db/user/events").json()
    assert events["events"]
    assert all(e["dues"]["userName"] not in ("", "You") for e in events["events"])
