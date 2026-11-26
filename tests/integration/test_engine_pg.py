"""Synthetic events in a temporary, rolled-back PostgreSQL schema."""

import os
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest
from psycopg import sql
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from services.engine.execution import first, process, reconcile
from services.engine.worker import backtest_step, recovery

T = datetime(2026, 1, 1, tzinfo=UTC)


@pytest.fixture
def db():
    uri = os.getenv("TEST_DATABASE_URL")
    if not uri:
        pytest.skip("Set TEST_DATABASE_URL to run isolated PostgreSQL integration tests")
    with psycopg.connect(uri, row_factory=dict_row) as conn:
        schema = sql.Identifier("test_engine_" + uuid4().hex)
        conn.execute(sql.SQL("CREATE SCHEMA {}").format(schema))
        conn.execute(sql.SQL("SET LOCAL search_path TO {}").format(schema))
        for name in ("001_market.sql", "020_engine.sql", "021_research_config.sql", "022_engine_build.sql"):
            conn.execute(Path("database/migrations", name).read_text())
        conn.execute(
            """INSERT INTO markets(market_id,condition_id,slug,title,asset,timeframe,start_time,end_time,
        up_token_id,down_token_id,resolution_source,settlement_rule,settlement_rule_version,rule_verified,
        market_status,accepting_orders,raw_metadata) VALUES('TEST','TEST','TEST','TEST','BTC',5,%s,%s,'u','d',
        'TEST','TEST','r1',true,'OPEN',true,'{}')""",
            (T, T + timedelta(minutes=5)),
        )
        yield conn
        conn.rollback()


def command(db, action="create", aid=None, p=None, key=None):
    return first(
        db, "SELECT pq_account_command(%s,%s,%s,%s,'test') a", (action, aid, Jsonb(p or {}), key or str(uuid4()))
    )["a"]


def book(db, ms, side, size="30", close=None):
    ts = T + timedelta(milliseconds=ms)
    data = {
        "side": side,
        "token_id": side,
        "asset": "BTC",
        "timeframe": 5,
        "start_time": T.isoformat(),
        "end_time": (close or T + timedelta(seconds=30)).isoformat(),
        "minimum_order_size": "5",
        "tick_size": "0.01",
        "asks": [["0.40", size]],
        "bids": [["0.39", "30"]],
        "fee_rate": "0.05",
        "fee_exponent": 1,
        "rule_verified": True,
        "rule_version": "r1",
        "accepting_orders": True,
    }
    return event(db, "BOOK", data, ts)


def event(db, kind, data, ts):
    return first(
        db,
        """INSERT INTO engine_events(event_id,market_id,source,kind,event_time,received_at,processed_at,data)
          VALUES(%s,'TEST','SYNTHETIC_TEST',%s,%s,%s,%s,%s) RETURNING *""",
        (str(uuid4()), kind, ts, ts, ts, Jsonb(data)),
    )


def active(db, mode="PAPER", cfg=None):
    a = command(db, p={"name": "TEST", "strategy_id": "S01", "mode": mode, "config": cfg or {}})
    command(db, "start", a["id"])
    return first(
        db, "UPDATE paper_accounts SET status='RUNNING',fresh_after=%s WHERE id=%s RETURNING *", (T, a["id"])
    )


def resolution(db, ms=700, verified=True):
    return event(
        db,
        "RESOLUTION",
        {"verified": verified, "outcome": "UP", "source": "TEST", "rule_version": "r1"},
        T + timedelta(milliseconds=ms),
    )


def test_control_idempotency_isolation_and_restart(db):
    p = {"name": "one", "strategy_id": "S01", "initial_balance": "1000.001"}
    a = command(db, p=p, key="create-1")
    assert command(db, p=p, key="create-1") == a
    with pytest.raises(psycopg.errors.RaiseException), db.transaction():
        command(db, p={**p, "name": "different"}, key="create-1")
    with pytest.raises(psycopg.errors.RaiseException), db.transaction():
        command(db, "resume", a["id"])
    clone = command(db, "clone", a["id"])
    assert a["id"] != clone["id"] and clone["available_balance"] == "1000.00100000"
    trading = active(db)
    for e in [book(db, 0, "UP"), book(db, 20, "DOWN")]:
        process(db, trading, e)
    assert (
        first(db, "SELECT frozen_balance FROM paper_accounts WHERE id=%s", (trading["id"],))["frozen_balance"] > 0
    )
    recovery(db)
    assert first(db, "SELECT status FROM paper_accounts WHERE id=%s", (trading["id"],))["status"] == "STARTING"
    assert reconcile(db, trading["id"])
    assert command(db, "pause", trading["id"])["frozen_balance"] == "0.00000000"
    assert command(db, "resume", trading["id"])["status"] == "STARTING"
    assert command(db, "stop", trading["id"])["status"] == "STOPPED"
    assert command(db, "archive", trading["id"])["status"] == "ARCHIVED"


def test_ioc_partial_no_lookahead_and_idempotent_settlement(db):
    a = active(db)
    stream = [book(db, 0, "UP"), book(db, 20, "DOWN"), book(db, 200, "UP", "4"), book(db, 550, "DOWN")]
    for e in stream:
        process(db, a, e)
    fills = db.execute("SELECT * FROM paper_fills").fetchall()
    assert len(fills) == 1 and fills[0]["quantity"] == 2
    orders = {r["side"]: r for r in db.execute("SELECT * FROM paper_orders")}
    assert orders["UP"]["status"] == "PARTIALLY_FILLED" and orders["DOWN"]["reason"] == "UNVERIFIABLE"
    assert reconcile(db, a["id"])
    process(db, a, resolution(db, 700, False))
    assert a["status"] == "RISK_HALTED" and first(db, "SELECT count(*) n FROM paper_settlements")["n"] == 0
    e = resolution(db, 800)
    process(db, a, e)
    process(db, a, e)
    assert first(db, "SELECT count(*) n FROM paper_settlements")["n"] == 1
    assert reconcile(db, a["id"])
    assert first(db, "SELECT available_balance FROM paper_accounts WHERE id=%s", (a["id"],))[
        "available_balance"
    ] == Decimal("1001.15581000")


def test_expiry_shadow_and_risk_halt(db):
    a = active(db)
    shadow = active(db, "SHADOW")
    close = T + timedelta(milliseconds=420)
    events = [book(db, 0, "UP", close=close), book(db, 20, "DOWN", close=close), book(db, 800, "UP")]
    for e in events:
        for account in [a, shadow]:
            process(db, account, e)
    assert not first(db, "SELECT * FROM paper_orders WHERE account_id=%s", (shadow["id"],))
    assert first(db, "SELECT count(*) n FROM paper_orders WHERE status='EXPIRED'")["n"] == 2
    process(db, a, event(db, "GAP", {}, T + timedelta(seconds=2)))
    assert a["status"] == "RISK_HALTED" and reconcile(db, a["id"])


def test_reproducible_replay_and_calibration(db):
    for ms, side in [(0, "UP"), (20, "DOWN"), (210, "UP"), (250, "DOWN"), (700, "UP")]:
        book(db, ms, side)
    resolution(db, 900)
    cfg = {"strategy_id": "S01", "start_time": T.isoformat(), "end_time": (T + timedelta(minutes=1)).isoformat()}
    outputs = []
    for _ in range(2):
        run = first(db, "INSERT INTO backtest_runs(config) VALUES(%s) RETURNING *", (Jsonb(cfg),))
        for _step in range(4):
            backtest_step(db)
        done = first(db, "SELECT * FROM backtest_runs WHERE id=%s", (run["id"],))
        assert done["status"] == "COMPLETED", done["result"]
        assert done["result"]["fills"] == 2 and done["result"]["market_probability_calibration"]["samples"] == 1
        assert reconcile(db, done["account_id"])
        outputs.append(done["result"])
    assert outputs[0] == outputs[1]


def test_strategy_versions_and_portfolio_bounds(db):
    db.execute("""INSERT INTO strategy_versions(strategy_id,version,parameters,implementation_hash)
         SELECT strategy_id,'1.1.0','{"tail_seconds":5}',implementation_hash FROM strategy_versions
         WHERE strategy_id='S02' AND version='1.0.0'""")
    cfg = {
        "strategies": [
            {"strategy_id": "S02", "strategy_version": "1.1.0"},
            {"strategy_id": "S03", "strategy_version": "1.0.0"},
        ]
    }
    a = active(db, "SHADOW", cfg)
    for e in [book(db, 0, "UP"), book(db, 20, "DOWN")]:
        process(db, a, e)
    rows = db.execute("SELECT strategy_id,strategy_version,reason FROM strategy_signals ORDER BY id").fetchall()
    assert any(
        r["strategy_id"] == "S02" and r["strategy_version"] == "1.1.0" and r["reason"] == "OUTSIDE_TAIL"
        for r in rows
    )
    assert {r["strategy_id"] for r in rows} == {"S01", "S02", "S03"}
    with pytest.raises(psycopg.errors.RaiseException), db.transaction():
        db.execute("UPDATE strategy_versions SET parameters='{}' WHERE version='1.1.0'")
    with pytest.raises(psycopg.errors.RaiseException), db.transaction():
        db.execute("UPDATE paper_accounts SET config='{}' WHERE id=%s", (a["id"],))
    assert reconcile(db, a["id"])


def test_daily_loss_limit_and_loss_run(db):
    a = active(db, cfg={"daily_loss": "0.0001", "max_drawdown": "0.05"})
    for e in [
        book(db, 0, "UP"),
        book(db, 20, "DOWN"),
        book(db, 200, "DOWN"),
        book(db, 700, "DOWN"),
        resolution(db, 900),
    ]:
        process(db, a, e)
    assert a["status"] == "RISK_HALTED"
    row = first(db, "SELECT max_drawdown,consecutive_losses FROM paper_accounts WHERE id=%s", (a["id"],))
    assert row["consecutive_losses"] == 1 and row["max_drawdown"] > 0
    assert reconcile(db, a["id"])


def test_changed_build_refuses_resume_or_replay(db):
    from services.engine.worker import live_step

    db.execute("""INSERT INTO strategy_versions(strategy_id,version,implementation_hash)
        VALUES('S00','0.0.1','unsupported-old-build')""")
    a = command(db, p={"name": "Old build", "strategy_id": "S00", "strategy_version": "0.0.1"})
    command(db, "start", a["id"])
    live_step(db, a["id"])
    assert first(db, "SELECT status FROM paper_accounts WHERE id=%s", (a["id"],))["status"] == "RISK_HALTED"
    assert (
        first(db, "SELECT reason FROM risk_events WHERE account_id=%s", (a["id"],))["reason"]
        == "IMPLEMENTATION_UNAVAILABLE"
    )
    book(db, 0, "UP")
    run = first(
        db, "INSERT INTO backtest_runs(config) VALUES(%s) RETURNING id", (Jsonb({"strategy_version": "0.0.1"}),)
    )
    backtest_step(db)
    assert first(db, "SELECT status FROM backtest_runs WHERE id=%s", (run["id"],))["status"] == "FAILED"


@pytest.fixture
def financial_db(db):
    for name in (
        "010_admin.sql",
        "030_operations.sql",
        "031_reports.sql",
        "032_evaluations.sql",
        "033_report_evidence.sql",
        "034_validation_evidence.sql",
    ):
        db.execute(Path("database/migrations", name).read_text())
    return db


def test_report_marks_open_exposure_unknown_and_deduplicates_counterfactuals(financial_db):
    db = financial_db
    a = active(db)
    clone = active(db)
    for e in [
        book(db, 0, "UP"),
        book(db, 20, "DOWN"),
        book(db, 200, "UP"),
        book(db, 210, "DOWN"),
        book(db, 700, "UP"),
    ]:
        for acct in (a, clone):
            process(db, acct, e)
    # Set fixture account inception to its logical research instant for period reporting.
    db.execute("UPDATE paper_accounts SET created_at=%s", (T,))
    before = first(db, "SELECT pq_report(NULL,%s,%s,'test-open')->'result' result", (T, T + timedelta(seconds=6)))[
        "result"
    ]
    assert before["net_pnl"] is None and before["valuation_quality"] == "MISSING_BOUNDARY_MARK"
    r = resolution(db, 8000)
    for acct in (a, clone):
        process(db, acct, r)
    db.execute(
        """INSERT INTO market_resolutions(market_id,outcome,rule_version,source,event_id,received_at)
       VALUES('TEST','UP','r1','TEST',%s,%s)""",
        (r["event_id"], r["received_at"]),
    )
    after = first(
        db, "SELECT pq_report(NULL,%s,%s,'test-closed')->'result' result", (T, T + timedelta(seconds=10))
    )["result"]
    assert after["settlements"] == 2 and after["probability_samples"] == 1


def test_evaluation_operating_cost_uses_market_units_and_window(financial_db):
    db = financial_db
    a = active(db)
    for ms, side in [(0, "UP"), (20, "DOWN"), (210, "UP"), (260, "DOWN"), (700, "UP")]:
        book(db, ms, side)
    resolution(db, 900)
    end = T + timedelta(seconds=1)
    cfg = {"start_time": T.isoformat(), "end_time": end.isoformat(), "strategy_id": "S01", "config": {}}
    ids = []
    for multiplier in ("1", "1.5"):
        cfg["config"] = {"cost_multiplier": multiplier}
        run = first(db, "INSERT INTO backtest_runs(config) VALUES(%s) RETURNING id", (Jsonb(cfg),))
        backtest_step(db)
        backtest_step(db)
        ids.append(str(run["id"]))
    exp = first(
        db,
        """INSERT INTO comparison_experiments(name,hypothesis,account_ids,criteria,snapshot,created_at)
       VALUES('TEST','TEST',ARRAY[%s]::uuid[],'TEST','{}',%s) RETURNING id""",
        (a["id"], T),
    )
    # Prior large profit must not appear in this evaluation window.
    db.execute(
        """INSERT INTO paper_settlements(account_id,market_id,outcome,payout,cost,realized_pnl,source,rule_version,event_id,created_at)
         VALUES(%s,'PAST','UP',10,1,9,'TEST','r1','TEST_PRIOR',%s)""",
        (a["id"], T - timedelta(days=1)),
    )
    data = {
        "account_id": str(a["id"]),
        "experiment_id": str(exp["id"]),
        "backtest_id": ids[0],
        "stress_backtest_id": ids[1],
        "checks": {},
        "cost_usd": "2",
        "evidence": "explicitly synthetic regression",
    }
    eval_row = first(db, "SELECT pq_evaluate(%s,'test') r", (Jsonb(data),))["r"]
    metrics = eval_row["metrics"]
    assert eval_row["status"] == "INSUFFICIENT_DATA" and metrics["out_of_sample_settled_fills"] == 2
    assert metrics["out_of_sample_settled_markets"] == 1 and Decimal(metrics["cost_per_settled_market"]) == 2
    assert metrics["paper_net_after_cost"] is None and metrics["observed_utc_days"] == 0
    with pytest.raises(psycopg.errors.RaiseException), db.transaction():
        data["cost_usd"] = "-100"
        first(db, "SELECT pq_evaluate(%s,'test') r", (Jsonb(data),))
