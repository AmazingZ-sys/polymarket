"""Offline tests. Copies of real rule documents are never inserted into production."""

import asyncio
import json
from datetime import timedelta
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from pqcollector.book import Book, quality
from pqcollector.markets import candidates, number, parse_market, stamp
from pqcollector.runtime import read_connection

EVIDENCE = Path(__file__).resolve().parents[2] / "docs/evidence/2026-11-26"
AT = stamp("2026-11-26T01:22:00Z")


def sample(asset="btc", minutes=5, ended=False):
    epoch = 1795648800 if ended else (1795656000 if minutes == 5 else 1795655700)
    return json.loads((EVIDENCE / f"{asset}-updown-{minutes}m-{epoch}.json").read_bytes())


@pytest.mark.parametrize(
    "asset,minutes,fee", [("btc", 5, "0.05"), ("eth", 5, "0.01"), ("btc", 15, "0.05"), ("eth", 15, "0.01")]
)
def test_independent_rules(asset, minutes, fee):
    raw = sample(asset, minutes)
    m = parse_market(raw, AT)
    assert m["slug"] in candidates(AT)
    assert m["rule_verified"] and m["fee_rate"] == fee and m["market_status"] == "ACTIVE"
    assert m["start_time"] != raw["startDate"]


def test_rule_drift_and_unverified_settlement():
    raw = sample(ended=True)
    assert parse_market(raw, AT)["outcome"] == "DOWN"
    raw["outcomePrices"] = '["0.0001","0.9999"]'
    assert parse_market(raw, AT)["outcome"] is None
    raw["description"] += " Additional exceptions apply."
    assert not parse_market(raw, AT)["rule_verified"]


def test_reverse_outcomes_ambiguous_tokens_and_tie():
    raw = sample(ended=True)
    tokens = json.loads(raw["clobTokenIds"])
    raw.update(outcomes='["Down","Up"]', clobTokenIds=json.dumps(tokens[::-1]), outcomePrices='["0","1"]')
    event = raw["events"][0]["eventMetadata"]
    event["finalPrice"] = event["priceToBeat"]
    parsed = parse_market(raw, AT)
    assert parsed["up_token_id"] == tokens[0] and parsed["outcome"] == "UP"
    raw["clobTokenIds"] = json.dumps(tokens[:1] * 2)
    with pytest.raises(ValueError, match="ambiguous"):
        parse_market(raw, AT)


def test_book_reconstruct_and_invalidation():
    b = Book()
    ts = "1795656001000"
    snap = {"timestamp": ts, "bids": [{"price": "0.44", "size": "9"}], "asks": [{"price": "0.51", "size": "3"}]}
    delta = [{"price": "0.44", "size": "0", "side": "BUY", "best_bid": "0", "best_ask": ".51"}]
    with pytest.raises(ValueError, match="MISSING_SNAPSHOT"):
        b.apply(snap, delta)
    b.apply(snap)
    b.apply(snap, delta)
    assert b.snapshot() == {"bids": [], "asks": [["0.51", "3"]]}
    with pytest.raises(ValueError, match="OUT_OF_ORDER"):
        b.apply(dict(snap, timestamp="1795656000000"))
    with pytest.raises(ValueError, match="TOP_MISMATCH"):
        b.apply(snap, [dict(delta[0], best_ask=".11")])


def test_time_and_invalid_prices():
    m = parse_market(sample(), AT)
    m["observed_at"] = AT - timedelta(seconds=121)
    assert quality(AT + timedelta(seconds=2), AT, m) == ["CLOCK_AHEAD", "STALE_METADATA"]
    assert "STALE_EVENT" in quality(AT - timedelta(seconds=4), AT, m)
    with pytest.raises(ValueError):
        number(float("nan"))
    with pytest.raises(ValueError, match="CROSSED_BOOK"):
        Book().apply(
            {
                "timestamp": "1795656000000",
                "asks": [{"price": "0.1", "size": 1}],
                "bids": [{"price": "0.3", "size": 1}],
            }
        )


def test_socket_invalid_message_requires_new_snapshot(monkeypatch):
    m = parse_market(sample(), AT)
    m["observed_at"] = AT
    connection = MagicMock()
    ws = AsyncMock()
    connection.__aenter__.return_value = ws
    ws.recv.return_value = json.dumps(
        {
            "event_type": "price_change",
            "timestamp": "1795656001000",
            "market": m["condition_id"],
            "price_changes": [{"asset_id": m["up_token_id"], "side": "BUY", "price": ".1", "size": "1"}],
        }
    )
    monkeypatch.setattr("pqcollector.runtime.websockets.connect", lambda *args, **kwargs: connection)
    discovery = MagicMock()
    subs = {m["condition_id"]: m}
    discovery.subscriptions.return_value = subs
    store = MagicMock()
    asyncio.run(read_connection(store, discovery, subs))
    assert "INVALID_BOOK" in store.gap.call_args.args and ws.send.await_count == 1


def test_licensed_oracle_provenance_and_causal_opening():
    from pqcollector.oracle import CHANNEL, OracleCache

    store = MagicMock()
    store.event.return_value = "test-only-evidence"
    m = parse_market(sample(), AT)
    opening = stamp(m["start_time"])
    ms = int(opening.timestamp() * 1000)
    frame = {
        "channel": CHANNEL,
        "v": 1,
        "seq": 2,
        "ts": ms,
        "payload": {
            "source": "chainlink",
            "symbol": "btcusd",
            "window_seconds": 60,
            "full_accuracy_value": "61000.123456789012345678",
            "timestamp": ms,
        },
    }
    cache = OracleCache()
    cache.ingest(store, frame, opening)
    result = cache.enrich(m, opening)
    assert result["reference_price"] == result["oracle_price"] == "61000.123456789012345678"
    assert result["oracle_event_id"] == "test-only-evidence"
    assert "oracle_price" not in cache.enrich(m, opening + timedelta(seconds=4))
    frame["payload"]["source"] = "wrong-provider"
    with pytest.raises(ValueError, match="UNVERIFIED"):
        cache.ingest(store, frame, opening)
    cache.clear()
    assert cache.enrich(m, opening) == {}
