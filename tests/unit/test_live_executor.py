"""Synthetic SDK/DB/HTTP doubles. No signer, chain, or order endpoint is contacted."""

import json
from contextlib import nullcontext
from datetime import UTC, datetime, timedelta
from decimal import Decimal as D
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import MagicMock

import httpx
import pytest

from services.live import model, venue, worker

T = datetime(2026, 11, 27, tzinfo=UTC)
WALLET = "0x" + "11" * 20
OWNER = "0x" + "22" * 20
SESSION = "0x" + "33" * 20
UP_ID = "0x" + "ab" * 32
DOWN_ID = "0x" + "cd" * 32


def test_mounted_secret_schema_and_scope(tmp_path):
    payload = {k: "synthetic" for k in model.Credentials.__annotations__}
    payload.update(
        wallet_address=WALLET, owner_address=OWNER, session_address=SESSION, session_private_key="44" * 32
    )
    keyfile = tmp_path / "test.json"
    keyfile.write_text(json.dumps(payload))
    cred = model.Credentials.load(keyfile)
    assert "synthetic" not in repr(cred)
    signer = {"address": SESSION, "scopes": ["CLOB"], "valid_until": int(T.timestamp()) + 60}
    raw = {"wallet": WALLET, "signers": [signer]}
    assert model.session_expiry(raw, cred, T) == T + timedelta(seconds=60)
    for scopes in ([], ["ALL"], ["CLOB", "PERPS"]):
        with pytest.raises(model.GuardError):
            model.session_expiry(dict(raw, signers=[dict(signer, scopes=scopes)]), cred, T)
    with pytest.raises(model.GuardError):
        model.session_expiry(dict(raw, wallet=OWNER), cred, T)
    with pytest.raises(model.GuardError):
        model.session_expiry(raw, cred, T + timedelta(seconds=60))
    keyfile.write_text(json.dumps(dict(payload, owner_private_key="never-accepted")))
    with pytest.raises(model.GuardError):
        model.Credentials.load(keyfile)


@pytest.mark.parametrize(
    "payload",
    [
        {"blocked": True, "ip": "192.0.2.1", "country": "US", "region": "NY"},
        {"blocked": False, "ip": "malformed", "country": "CA", "region": "AB"},
        {"blocked": False, "ip": "192.0.2.1", "country": "", "region": ""},
    ],
)
def test_denied_or_unverifiable_geography_precedes_all_auth(payload, monkeypatch):
    # Isolate the mock client from the host developer's network environment.
    for key in ("http_proxy", "https_proxy", "all_proxy", "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY"):
        monkeypatch.delenv(key, raising=False)
    calls = []

    def response(request):
        calls.append(request)
        return httpx.Response(200, json=payload)

    credentials = NS(wallet_address=WALLET)
    adapter = venue.Venue(credentials)
    adapter.http.close()
    adapter.http = httpx.Client(transport=httpx.MockTransport(response))
    try:
        with pytest.raises(ValueError):
            adapter.authorize(WALLET)
        assert len(calls) == 1 and str(calls[0].url) == venue.GEO_URL
        assert not any(k.startswith("poly_") for k in calls[0].headers)
    finally:
        adapter.close()


def test_allowance_for_actual_exchange_and_funds(monkeypatch):
    adapter = venue.Venue(NS())
    fake = MagicMock()
    adapter.client = fake
    monkeypatch.setattr(adapter, "authorize", lambda wallet: T)
    fake.get_closed_only_mode.return_value = False
    fake.list_wallet_open_orders.return_value.iter_items.side_effect = lambda: iter(())
    balance = NS(balance=3_000_000, allowances={venue.EXCHANGE: 2**256 - 1})
    fake.get_balance_allowance.return_value = balance
    assert adapter.account(WALLET) == (T, D(3))
    balance.allowances = {OWNER: 2**256 - 1}
    with pytest.raises(model.GuardError, match="FUNDS"):
        adapter.account(WALLET)
    fake.list_wallet_open_orders.return_value.iter_items.side_effect = lambda: iter((object(),))
    with pytest.raises(model.GuardError, match="OPEN_ORDERS"):
        adapter.account(WALLET)
    adapter.close()


def inputs():
    market = {
        "up_token_id": "101",
        "down_token_id": "102",
        "asset": "BTC",
        "timeframe": 5,
        "condition_id": "condition",
        "rule_version": "rule",
        "start_time": (T - timedelta(seconds=1)).isoformat(),
        "end_time": (T + timedelta(minutes=2)).isoformat(),
        "fee_rate": "0.05",
        "fee_exponent": 1,
    }
    books = []
    entries = []
    for side, token, price in (("DOWN", "102", "0.46"), ("UP", "101", "0.39")):
        data = {
            "rule_verified": True,
            "accepting_orders": True,
            "rule_version": "rule",
            "token_id": token,
            "side": side,
            "asset": "BTC",
            "timeframe": 5,
        }
        entries.append({"event_id": side, "book": data})
        books.append(
            NS(
                condition_id="condition",
                neg_risk=False,
                asset_id=token,
                timestamp=T,
                min_order_size=D(5),
                tick_size=D(".01"),
                asks=(NS(price=D(price), size=D(100)),),
                bids=(NS(price=D(price) - D(".01"), size=D(100)),),
            )
        )
    row = {
        "strategy_id": "S01",
        "account_strategy": "S01",
        "strategy_version": "v1",
        "account_version": "v1",
        "action": "PAIR",
        "reason": "POSITIVE_THEORETICAL_EDGE",
        "config": {"impact_ticks": 1, "depth_fraction": "0.5"},
        "created_at": T,
        "evidence": {"quantity": "21", "books": entries, "limits": ["0.47", "0.40"]},
    }
    return row, market, books


def test_down_first_evidence_reordered_sized_within_principal_and_both_leg_budget():
    row, market, books = inputs()
    pair = model.plan_pair(row, market, books, T, D("8.99"), D("12.19"))
    assert pair.quantity == 14
    assert pair.legs == (model.Leg("101", D(".40"), D("5.60")), model.Leg("102", D(".47"), D("6.58")))
    assert pair.legs[0].principal + pair.legs[1].principal == D("12.18")
    row["account_strategy"] = "S00"
    row["config"]["strategies"] = [
        {"strategy_id": "S01", "strategy_version": "v1", "parameters": {"minimum_edge": "0.3"}}
    ]
    with pytest.raises(model.GuardError, match="NO_NET_EDGE"):
        model.plan_pair(row, market, books, T, D(12), D(30))


@pytest.mark.parametrize("change", ["stale", "crossed", "token", "neg_risk", "rule", "depth", "signal"])
def test_unusable_book_and_signal_withheld(change):
    row, market, books = inputs()
    if change == "stale":
        books[0].timestamp = T - timedelta(seconds=4)
    elif change == "crossed":
        books[0].bids = books[0].asks
    elif change == "token":
        row["evidence"]["books"][0]["book"]["token_id"] = "101"
    elif change == "neg_risk":
        books[1].neg_risk = True
    elif change == "rule":
        row["evidence"]["books"][0]["book"]["rule_version"] = "different"
    elif change == "depth":
        books[1].asks[0].size = D(1)
    else:
        row["action"] = "OBSERVE"
    with pytest.raises(ValueError):
        model.plan_pair(row, market, books, T, D(20), D(40))


def fake_order(leg, quantity):
    return NS(
        maker=WALLET,
        signer=WALLET,
        signature_type=3,
        order_type="FOK",
        post_only=False,
        side="BUY",
        token_id=leg.token,
        maker_amount=int(leg.principal * 1_000_000),
        taker_amount=int(quantity * 1_000_000),
    )


def fake_db(claim=True, gate=True, attempts=()):
    db = MagicMock()

    def execute(sql, args=()):
        cursor = MagicMock()
        if "pq_live_reserve" in sql:
            cursor.fetchone.return_value = {"ok": claim}
        elif "pq_live_dispatch_ready" in sql:
            cursor.fetchone.return_value = {"ok": gate}
        elif "FROM polymarket_live_attempts" in sql:
            cursor.fetchall.return_value = attempts
        return cursor

    db.execute.side_effect = execute
    db.transaction.side_effect = nullcontext
    return db


def records(db, name):
    return [c.args[1] for c in db.execute.call_args_list if name in c.args[0]]


def fake_venue(pair):
    adapter = MagicMock()
    adapter.credentials.wallet_address = WALLET
    adapter.account.return_value = (T + timedelta(seconds=60), D(80))
    adapter.client.create_market_order.side_effect = [fake_order(leg, pair.quantity) for leg in pair.legs]
    return adapter


@pytest.mark.parametrize(
    "case,expected",
    [
        ("accept", "RESERVED"),
        ("reject", "REJECTED"),
        ("one", "EXPOSED"),
        ("timeout", "UNKNOWN"),
        ("bad_batch", "UNKNOWN"),
        ("unknown_reject", "UNKNOWN"),
        ("cap", "REJECTED"),
        ("halt", "REJECTED"),
        ("expired", "REJECTED"),
        ("duplicate", None),
    ],
)
def test_at_most_one_synthetic_batch_and_crash_quarantine(monkeypatch, case, expected):
    row, market, books = inputs()
    row["id"] = 7
    pair = model.plan_pair(row, market, books, T, D(10), D(20))
    db = fake_db(gate=case != "halt", claim=case != "duplicate")
    adapter = fake_venue(pair)
    monkeypatch.setattr(worker, "now", lambda: T + timedelta(seconds=4 if case == "expired" else 0))
    pos = [NS(ok=True, order_id=i) for i in (UP_ID, DOWN_ID)]
    reject = NS(ok=False, code="fok_not_filled")
    post = adapter.client.post_orders
    if case == "timeout":
        post.side_effect = TimeoutError()
    elif case == "bad_batch":
        post.return_value = pos[:1]
    elif case == "reject":
        post.return_value = (reject, reject)
    elif case == "one":
        post.return_value = (reject, pos[1])
    elif case == "unknown_reject":
        post.return_value = (NS(ok=False, code="unknown"), reject)
    else:
        post.return_value = pos
    if case == "cap":
        invalid = fake_order(pair.legs[0], pair.quantity)
        invalid.maker_amount += 1
        adapter.client.create_market_order.side_effect = [invalid]
    monkeypatch.setattr(worker, "reconcile_one", lambda *args: None)
    if case in ("timeout", "bad_batch", "cap", "halt", "expired"):
        with pytest.raises((ValueError, TimeoutError)):
            worker.dispatch(db, adapter, {"revision": 2, "wallet_address": WALLET}, row, pair)
    else:
        worker.dispatch(db, adapter, {"revision": 2, "wallet_address": WALLET}, row, pair)
    assert post.call_count == (0 if case in ("cap", "halt", "expired", "duplicate") else 1)
    states = [r[1] for r in records(db, "pq_live_result")]
    assert states == ([expected] if expected not in ("RESERVED", None) else [])
    if case == "accept":
        assert records(db, "pq_live_ack") == [(7, UP_ID, DOWN_ID, False, False)]


def test_restart_never_reuses_old_request_without_durable_ids(monkeypatch):
    row = {
        "signal_id": 42,
        "state": "RESERVED",
        "up_order_id": None,
        "down_order_id": None,
        "up_principal": None,
        "down_principal": None,
    }
    db = fake_db(attempts=[row])
    adapter = MagicMock()
    adapter.credentials.wallet_address = WALLET
    worker.recover(db, adapter)
    assert records(db, "pq_live_result")[0][1] == "UNKNOWN"
    assert not adapter.client.post_orders.called
    assert not adapter.authorize.called


def test_restart_reconciles_existing_ids_read_only(monkeypatch):
    row = {
        "signal_id": 42,
        "state": "RESERVED",
        "wallet_address": WALLET,
        "up_order_id": UP_ID,
        "down_order_id": DOWN_ID,
        "up_principal": None,
        "down_principal": None,
        "updated_at": T,
    }
    db = fake_db(attempts=[row])
    adapter = MagicMock()
    adapter.credentials.wallet_address = WALLET
    from services.live import settlement

    monkeypatch.setattr(settlement, "classify", lambda *args, **kwargs: ("RESERVED", UP_ID, DOWN_ID, *[None] * 6))
    monkeypatch.setattr(worker, "now", lambda: T + timedelta(seconds=30))
    worker.recover(db, adapter)
    assert not records(db, "pq_live_result")
    monkeypatch.setattr(worker, "now", lambda: T + timedelta(seconds=91))
    worker.recover(db, adapter)
    assert records(db, "pq_live_result")[0][1] == "UNKNOWN"
    assert not adapter.client.post_orders.called


def test_captured_ctf_market_rule_and_scope_are_reverified():
    raw = json.loads(Path("docs/evidence/2026-11-26/btc-updown-5m-1795656000.json").read_text())
    at = datetime.fromisoformat(raw["eventStartTime"]) + timedelta(seconds=30)
    observed = model.parse_market(raw, at)
    row = dict(
        observed, settlement_rule_version=observed["rule_version"], rule_changed=False, resolved_outcome=None
    )
    assert model.verify_market(row, raw, at)["rule_verified"]
    for field, value in (
        ("version", "v2"),
        ("conditionId", "0x" + "00" * 32),
        ("description", "new rule"),
        ("tradingEligible", False),
        ("acceptingOrders", False),
    ):
        with pytest.raises(model.GuardError):
            model.verify_market(row, dict(raw, **{field: value}), at)


def test_authorization_uses_only_owner_scope_read_and_validates_session_credential(monkeypatch):
    from eth_account import Account
    from polymarket import SecureClient
    from polymarket._internal.hmac import build_hmac_signature

    for key in ("http_proxy", "https_proxy", "all_proxy", "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY"):
        monkeypatch.delenv(key, raising=False)
    cred = model.Credentials(
        wallet_address=WALLET,
        owner_address=OWNER,
        owner_api_key="fixture-owner",
        owner_api_passphrase="fixture-pass",
        owner_api_secret="QUJD",
        session_address=SESSION,
        session_private_key="KEY_FIXTURE_REPLACED",
        session_api_key="fixture-session",
        session_api_secret="R0hJ",
        session_api_passphrase="fixture-session-pass",
    )
    requests = []

    def fake_http(request):
        requests.append(request)
        if str(request.url) == venue.GEO_URL:
            return httpx.Response(200, json={"blocked": False, "country": "CA", "region": "AB", "ip": "192.0.2.2"})
        assert request.method == "GET" and str(request.url) == "https://clob.polymarket.com" + venue.SCOPE_PATH
        headers = request.headers
        assert headers["poly_address"] == OWNER and headers["poly_api_key"] == cred.owner_api_key
        stamp = int(headers["poly_timestamp"])
        assert headers["poly_signature"] == build_hmac_signature(
            secret="QUJD", timestamp=stamp, method="GET", path=venue.SCOPE_PATH
        )
        return httpx.Response(
            200,
            json={
                "wallet": WALLET,
                "signers": [{"address": SESSION, "scopes": ["CLOB"], "valid_until": stamp + 600}],
            },
        )

    fake_sdk = MagicMock()
    fake_sdk.wallet, fake_sdk.signer, fake_sdk.wallet_type = WALLET, SESSION, "DEPOSIT_WALLET"
    fake_sdk.fetch_api_keys.return_value = (cred.session_api_key,)
    factory = MagicMock(return_value=fake_sdk)
    monkeypatch.setattr(Account, "from_key", lambda key: NS(address=SESSION))
    monkeypatch.setattr(SecureClient, "_create", factory)
    adapter = venue.Venue(cred)
    adapter.http.close()
    adapter.http = httpx.Client(transport=httpx.MockTransport(fake_http))
    try:
        assert adapter.authorize(WALLET) > worker.now()
        assert len(requests) == 2
        assert factory.call_args.kwargs["credentials"].key == "fixture-session"
        assert factory.call_args.kwargs["validate_credentials"] is False
        fake_sdk.fetch_api_keys.assert_called_once()
        fake_sdk.fetch_api_keys.return_value = ()
        with pytest.raises(model.GuardError, match="SESSION_CREDENTIALS"):
            adapter.authorize(WALLET)
        factory.assert_called_once()
    finally:
        adapter.close()
