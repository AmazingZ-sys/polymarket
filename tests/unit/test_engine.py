"""Synthetic, offline research fixtures only."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal as D

import pytest

from services.engine.evaluation import calibration, daily_block_interval, time_weighted_mean
from services.engine.model import Book, fee, match, opportunity
from services.engine.research import observe

TS = datetime(2026, 1, 1, tzinfo=UTC)
CFG = {"impact_ticks": 1, "depth_fraction": "0.5", "tail_seconds": 60}


def fixture_book(side="UP", **overrides):
    data = {
        "side": side,
        "token_id": side,
        "asset": "BTC",
        "timeframe": 5,
        "asks": [["0.40", "30"]],
        "bids": [["0.39", "20"]],
        "tick_size": "0.01",
        "minimum_order_size": "5",
        "fee_rate": "0.05",
        "fee_exponent": 1,
        "rule_verified": True,
        "rule_version": "TEST_RULE",
        "accepting_orders": True,
        "start_time": (TS - timedelta(minutes=5)).isoformat(),
        "end_time": (TS + timedelta(seconds=20)).isoformat(),
        **overrides,
    }
    return {
        "data": data,
        "kind": "BOOK",
        "event_id": "TEST_" + side,
        "event_time": TS,
        "received_at": TS,
        "quality_flags": [],
    }


def test_conservative_depth_price_and_precision():
    b = Book.parse(fixture_book(), TS)
    partial = match(b, CFG, D(20), D(".41"))
    assert (partial.quantity, partial.notional, partial.slippage) == (D(15), D("6.15"), D(".15"))
    assert partial.fees == D(".181425")
    assert match(b, CFG, D(20), D(".40")).quantity == 0
    assert fee(D(100), D(".5"), D(".05")) == D("1.25")


def test_pair_minimum_depth_and_gross_edge():
    books = [Book.parse(fixture_book(s), TS) for s in ("UP", "DOWN")]
    q, limits, reason = opportunity(books, CFG, D(5))
    assert q >= 5 and limits == [D(".41")] * 2 and "POSITIVE" in reason
    assert opportunity(books, CFG, D(".1"))[0] == 0
    wrong = Book.parse(fixture_book("DOWN", rule_version="OTHER"), TS)
    assert opportunity([books[0], wrong], CFG, D(100))[0] == 0


@pytest.mark.parametrize(
    "change",
    [
        {"fee_rate": None},
        {"accepting_orders": False},
        {"rule_verified": False},
        {"bids": [["0.80", "1"]]},
        {"tick_size": "0"},
        {"asks": [[".403", "20"]]},
        {"asks": []},
        {"fee_exponent": 2},
    ],
)
def test_invalid_books_fail_closed(change):
    from services.engine.execution import validated

    assert validated(fixture_book(**change), TS) is None


def test_future_data_and_stale_data():
    from services.engine.execution import validated

    assert validated(fixture_book(), TS - timedelta(microseconds=1)) is None
    assert validated(fixture_book(), TS + timedelta(seconds=4)) is None


def test_observation_strategies():
    b = Book.parse(fixture_book(), TS)
    assert observe("S00", b, CFG)[0] == "NO_TRADE_BASELINE"
    assert observe("S02", b, CFG)[0] == "TAIL_OBSERVATION"
    assert observe("S03", b, CFG)[1]["market_probability"] == "0.395"
    assert observe("S04", b, CFG)[0] == "AWAITING_OVERLAPPING_HORIZON"
    assert (
        observe("S04", b, CFG, {"event_id": "TEST", "market_id": "B", "p": "0.5"})[1]["probability_spread"]
        == "-0.105"
    )


def test_statistics_require_coverage():
    assert calibration([])["brier"] is None
    report = calibration([(0.5, 1), (0.5, 0)])
    assert report["brier"] == 0.25 and report["ece"] == 0 and report["samples"] == 2
    assert daily_block_interval([(TS, D(1))], 17) is None
    assert daily_block_interval([(TS + timedelta(days=i), D(1)) for i in range(10)], 17) == [1, 1]
    end = TS + timedelta(seconds=2)
    prices = [(TS, D(10)), (TS + timedelta(seconds=1), D(11)), (end, D(20))]
    assert time_weighted_mean(prices, TS, end, 1) == D("10.5")
    assert time_weighted_mean([prices[0], prices[-1]], TS, end, 1) is None


def test_walk_forward_purges_labels_and_embargo():
    from services.engine.evaluation import walk_forward

    labels = [
        {
            "market_id": str(i),
            "decision_time": TS + timedelta(hours=i),
            "outcome_time": TS + timedelta(hours=i, minutes=5),
            "p": "0.5",
            "y": i % 2,
        }
        for i in range(16)
    ]
    for item in labels[:3]:
        item["outcome_time"] = TS + timedelta(days=2)
    folds = walk_forward(labels, 3, 900)
    assert folds[0]["train_count"] == 1 and folds[0]["test_count"] == 4
    assert folds[-1]["final_holdout"] is True
    assert walk_forward([]) == []


def test_tail_oracle_provenance_and_future_rejection():
    inputs = {
        "reference_price": "100",
        "oracle_price": "99",
        "oracle_event_id": "TEST_PRICE",
        "twap_window": 60,
        "oracle_window": 60,
        "oracle_source": "chainlink",
        "oracle_event_time": TS.isoformat(),
        "oracle_received_at": TS.isoformat(),
    }
    b = Book.parse(fixture_book(**inputs), TS)
    assert observe("S02", b, CFG)[1]["observed_margin"] == "-1"
    late = {**inputs, "oracle_received_at": (TS + timedelta(seconds=1)).isoformat()}
    assert observe("S02", Book.parse(fixture_book(**late), TS), CFG)[1]["oracle_coverage"] == "UNVERIFIED"


def test_reference_correlation_only_synchronous_pairs():
    from services.engine.evaluation import reference_correlation

    rows = [
        {"received_at": TS + timedelta(seconds=i), "asset": a, "price": str(100 + i * i)}
        for i in range(15)
        for a in ("ETH", "BTC")
    ]
    assert reference_correlation(rows)["correlation"] == pytest.approx(1)
    assert reference_correlation(rows[::2])["correlation"] is None
