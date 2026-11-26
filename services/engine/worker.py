"""Single-writer worker; PostgreSQL transactions bind each cursor to its economic outputs."""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import signal
import time
from datetime import UTC, datetime

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from .build import engine_hash, supports
from .evaluation import calibration, daily_block_interval, reference_correlation, walk_forward
from .execution import execute_due, first, halt, process, reconcile
from .model import iso

LOG = logging.getLogger("polyquant.engine")
BATCH = 128


def recovery(conn):
    """Never cross a service outage with live orders. Resume a clean account after a new book."""
    ts = datetime.now(UTC)
    for a in conn.execute(
        "SELECT * FROM paper_accounts WHERE mode<>'BACKTEST' AND status='RUNNING' FOR UPDATE"
    ).fetchall():
        conn.execute("SELECT pq_cancel(%s,'WORKER_RECONNECT',%s)", (a["id"], ts))
        if not reconcile(conn, a["id"]):
            halt(conn, a, "LEDGER_MISMATCH", ts)
        else:
            conn.execute("UPDATE paper_accounts SET status='STARTING',fresh_after=%s WHERE id=%s", (ts, a["id"]))
            conn.execute(
                """INSERT INTO paper_sessions(account_id,from_status,status,actor,reason)
                VALUES(%s,'RUNNING','STARTING','engine','WORKER_RECONNECT')""",
                (a["id"],),
            )


def live_step(conn, aid):
    a = first(conn, "SELECT * FROM paper_accounts WHERE id=%s FOR UPDATE", (aid,))
    if a["mode"] == "BACKTEST" or a["status"] not in {
        "STARTING",
        "RUNNING",
        "PAUSED",
        "STOPPED",
        "RISK_HALTED",
        "ERROR",
    }:
        return 0
    wall = datetime.now(UTC)
    if a["status"] in {"STARTING", "RUNNING"} and not supports(a["config"]):
        halt(conn, a, "IMPLEMENTATION_UNAVAILABLE", wall)
        return 0
    if a["status"] == "STARTING" and not reconcile(conn, aid):
        halt(conn, a, "LEDGER_MISMATCH", wall)
        return 0
    if a["status"] == "STARTING" and a["last_seq"] == 0:
        # A newly created account has no positions or earlier decisions to reconstruct.
        a["last_seq"] = first(
            conn, "SELECT coalesce(max(seq),0) n FROM engine_events WHERE received_at<%s", (a["fresh_after"],)
        )["n"]
        conn.execute("UPDATE paper_accounts SET last_seq=%s WHERE id=%s", (a["last_seq"], aid))
    rows = conn.execute(
        """SELECT * FROM engine_events WHERE seq>%s AND received_at<=%s AND kind IN ('BOOK','RESOLUTION','GAP')
                           ORDER BY seq LIMIT %s""",
        (a["last_seq"], wall, BATCH),
    ).fetchall()
    for e in rows:
        age = (wall - e["received_at"]).total_seconds()
        fresh = 0 <= age <= 3 and (not a["fresh_after"] or e["received_at"] >= a["fresh_after"])
        if a["status"] == "STARTING" and fresh and e["kind"] == "BOOK":
            from .execution import validated

            b = validated(e, wall)
            if b and b.data["asset"] in a["config"]["assets"] and b.data["timeframe"] in a["config"]["timeframes"]:
                conn.execute(
                    "UPDATE paper_accounts SET status='RUNNING',started_at=coalesce(started_at,%s) WHERE id=%s",
                    (wall, aid),
                )
                conn.execute(
                    """INSERT INTO paper_sessions(account_id,from_status,status,actor,reason)
                   VALUES(%s,'STARTING','RUNNING','engine','NEW_VERIFIED_BOOK')""",
                    (aid,),
                )
                a["status"] = "RUNNING"
        if not fresh and a["status"] == "RUNNING":
            conn.execute("SELECT pq_cancel(%s,'LAGGED_OBSERVATION',%s)", (aid, wall))
        process(conn, a, e, allow_signals=fresh)
    if a["status"] == "RUNNING" and len(rows) < BATCH:
        if a["last_book_at"] is None or (wall - a["last_book_at"]).total_seconds() > 10:
            halt(conn, a, "STREAM_STALE", wall)
        else:
            execute_due(conn, a, wall, a["last_seq"])
    return len(rows)


def initialize_backtest(conn, run):
    cfg = run["config"]
    start = iso(cfg["start_time"]) if cfg.get("start_time") else datetime(1970, 1, 1, tzinfo=UTC)
    end = iso(cfg["end_time"]) if cfg.get("end_time") else run["created_at"]
    if start >= end:
        raise ValueError("start_time must precede end_time")
    boundary = first(
        conn,
        """SELECT coalesce(min(seq),0) first,coalesce(max(seq),0) last,count(*) event_count
       FROM engine_events WHERE kind IN ('BOOK','GAP','RESOLUTION','ORACLE','REFERENCE','TRADE','METADATA','DATA_QUALITY') AND received_at BETWEEN %s AND %s""",
        (start, end),
    )
    if not boundary["event_count"]:
        conn.execute(
            """UPDATE backtest_runs SET status='COMPLETED',progress=100,result=%s,finished_at=now()
                WHERE id=%s""",
            (Jsonb({"validation": "INSUFFICIENT_DATA", "event_count": 0, "code_hash": engine_hash()}), run["id"]),
        )
        return
    payload = {
        "mode": "BACKTEST",
        "name": "Backtest " + str(run["id"])[:8],
        "initial_balance": cfg.get("initial_balance", "1000"),
        "strategy_id": cfg.get("strategy_id", "S00"),
        "strategy_version": cfg.get("strategy_version", "1.0.0"),
        "config": cfg.get("config", {}),
    }
    account = first(
        conn, "SELECT pq_account_command('create',NULL,%s,%s,'backtest') AS a", (Jsonb(payload), str(run["id"]))
    )["a"]
    if not supports(account["config"]):
        raise ValueError("Versioned research implementation unavailable")
    conn.execute(
        """UPDATE paper_accounts SET status='RUNNING',started_at=%s,fresh_after=%s,last_seq=%s WHERE id=%s""",
        (start, start, boundary["first"] - 1, account["id"]),
    )
    conn.execute("UPDATE equity_snapshots SET created_at=%s WHERE account_id=%s", (start, account["id"]))
    conn.execute(
        "UPDATE balance_ledger SET created_at=%s WHERE account_id=%s AND reason='INITIAL'", (start, account["id"])
    )
    conn.execute(
        """INSERT INTO paper_sessions(account_id,from_status,status,actor,reason,created_at)
       VALUES(%s,'CREATED','RUNNING','backtest','REPLAY_START',%s)""",
        (account["id"], start),
    )
    conn.execute(
        """UPDATE backtest_runs SET status='RUNNING',account_id=%s,boundary_seq=%s,first_seq=%s,result=%s WHERE id=%s""",
        (
            account["id"],
            boundary["last"],
            boundary["first"],
            Jsonb(
                {
                    "start_time": start.isoformat(),
                    "end_time": end.isoformat(),
                    "event_count": boundary["event_count"],
                    "data_hash": "0" * 64,
                    "event_counts": {},
                    "code_hash": engine_hash(),
                    "seed": int(cfg.get("seed", 17)),
                }
            ),
            run["id"],
        ),
    )


def finish_backtest(conn, run, account):
    aid, r = account["id"], run["result"]
    asof = account["last_seen"] or account["fresh_after"]
    conn.execute("SELECT pq_cancel(%s,'END_OF_DATA',%s)", (aid, asof))
    totals = first(
        conn,
        """SELECT
        (SELECT count(*) FROM paper_fills WHERE account_id=%s) fills,
        (SELECT count(*) FROM paper_orders WHERE account_id=%s AND filled_quantity=0) unfilled,
        (SELECT count(*) FROM paper_orders WHERE account_id=%s AND status='PARTIALLY_FILLED') partial,
        (SELECT count(*) FROM strategy_signals WHERE account_id=%s) signals""",
        (aid, aid, aid, aid),
    )
    settlements = conn.execute(
        """SELECT created_at,realized_pnl FROM paper_settlements
                                   WHERE account_id=%s ORDER BY created_at,market_id""",
        (aid,),
    ).fetchall()
    labels = conn.execute(
        """SELECT DISTINCT ON(s.market_id) s.market_id,s.created_at decision_time,e.received_at outcome_time,s.evidence->>'market_probability' p,
      (e.data->>'outcome'='UP')::int y FROM strategy_signals s JOIN engine_events e
      ON e.market_id=s.market_id AND e.kind='RESOLUTION' AND e.data->>'verified'='true'
      AND e.data->>'outcome' IN ('UP','DOWN') AND e.data->>'rule_version'=s.evidence->'book'->>'rule_version'
      AND cardinality(e.quality_flags)=0 AND e.seq<=%s AND e.received_at BETWEEN s.created_at AND %s
      WHERE s.account_id=%s ORDER BY s.market_id,s.created_at,e.seq""",
        (run["boundary_seq"], r["end_time"], aid),
    ).fetchall()
    s04 = account["strategy_id"] == "S04" or any(
        x["strategy_id"] == "S04" for x in account["config"].get("strategies", [])
    )
    if s04:
        prices = conn.execute(
            """SELECT received_at,data->>'asset' asset,data->>'price' price FROM engine_events
            WHERE kind='REFERENCE' AND source='kraken' AND data->>'price' IS NOT NULL
            AND data->>'asset' IN ('BTC','ETH') AND seq BETWEEN %s AND %s AND received_at BETWEEN %s AND %s
            ORDER BY seq""",
            (run["first_seq"], run["boundary_seq"], r["start_time"], r["end_time"]),
        ).fetchall()
        r["cross_asset_reference"] = reference_correlation(prices)
    tail = conn.execute(
        """SELECT s.evidence->'book'->>'asset' asset,s.evidence->'book'->>'timeframe' timeframe,
       s.evidence->'book'->>'rule_version' rule_version,s.reason window_group,s.evidence->>'oracle_coverage' oracle_coverage,
       count(*) observations,count(DISTINCT s.market_id) markets FROM strategy_signals s WHERE account_id=%s AND strategy_id='S02'
       GROUP BY 1,2,3,4,5 ORDER BY 1,2,3,4,5""",
        (aid,),
    ).fetchall()
    r["tail_strata"] = tail
    r.update(totals)
    r.update(
        validation="INSUFFICIENT_DATA",
        equity=str(account["equity"]),
        fees=str(account["fees"]),
        net_pnl=str(account["equity"] - account["initial_balance"]),
        max_drawdown=str(account["max_drawdown"]),
        config_hash=account["config_hash"],
        settled_markets=len(settlements),
        walk_forward=walk_forward(labels),
        market_probability_calibration=calibration([(float(x["p"]), x["y"]) for x in labels]),
        mean_pnl_daily_block_ci=daily_block_interval(
            [(x["created_at"], x["realized_pnl"]) for x in settlements], r["seed"]
        ),
    )
    conn.execute(
        "UPDATE backtest_runs SET progress=100,status='COMPLETED',result=%s,finished_at=now() WHERE id=%s",
        (Jsonb(r), run["id"]),
    )
    conn.execute("UPDATE paper_accounts SET status='STOPPED',stopped_at=%s WHERE id=%s", (asof, aid))


def backtest_step(conn):
    cancelled = conn.execute("""SELECT r.id,a.id aid FROM backtest_runs r JOIN paper_accounts a ON a.id=r.account_id
                       WHERE r.status='CANCELLED' AND a.status IN ('RUNNING','RISK_HALTED') FOR UPDATE OF r,a""").fetchall()
    for c in cancelled:
        conn.execute("SELECT pq_cancel(%s,'REPLAY_CANCELLED',now())", (c["aid"],))
        conn.execute("UPDATE paper_accounts SET status='STOPPED',stopped_at=now() WHERE id=%s", (c["aid"],))
        conn.execute("UPDATE backtest_runs SET finished_at=now() WHERE id=%s", (c["id"],))
    run = first(
        conn,
        """SELECT * FROM backtest_runs WHERE status IN ('QUEUED','RUNNING')
                        ORDER BY created_at,id LIMIT 1 FOR UPDATE SKIP LOCKED""",
    )
    if not run:
        return 0
    try:
        with conn.transaction():
            if run["status"] == "QUEUED":
                initialize_backtest(conn, run)
                return 1
            a = first(conn, "SELECT * FROM paper_accounts WHERE id=%s FOR UPDATE", (run["account_id"],))
            r = run["result"]
            if r["code_hash"] != engine_hash() or not supports(a["config"]):
                raise ValueError("Pinned research implementation unavailable")
            events = conn.execute(
                """SELECT * FROM engine_events WHERE seq BETWEEN %s AND %s AND kind IN ('BOOK','GAP','RESOLUTION','ORACLE','REFERENCE','TRADE','METADATA','DATA_QUALITY') AND received_at
              BETWEEN %s AND %s ORDER BY seq LIMIT %s""",
                (a["last_seq"] + 1, run["boundary_seq"], r["start_time"], r["end_time"], BATCH),
            ).fetchall()
            for event in events:
                process(conn, a, event)
                kind = event["kind"]
                counts = r["event_counts"]
                counts[kind] = counts.get(kind, 0) + 1
                content = [
                    str(event["seq"]),
                    kind,
                    event["market_id"],
                    event["event_id"],
                    event["source"],
                    event["event_time"].astimezone(UTC).isoformat(),
                    event["received_at"].astimezone(UTC).isoformat(),
                    event["quality_flags"],
                    event["data"],
                ]
                raw = json.dumps(content, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
                r["data_hash"] = hashlib.sha256(
                    bytes.fromhex(r["data_hash"]) + hashlib.sha256(raw).digest()
                ).hexdigest()
            if len(events) < BATCH:
                finish_backtest(conn, run, a)
            else:
                fraction = (a["last_seq"] - run["first_seq"] + 1) / (run["boundary_seq"] - run["first_seq"] + 1)
                conn.execute(
                    "UPDATE backtest_runs SET progress=%s,result=%s WHERE id=%s",
                    (min(99, fraction * 100), Jsonb(r), run["id"]),
                )
    except (
        ValueError,
        KeyError,
        psycopg.DataError,
        psycopg.IntegrityError,
        psycopg.errors.RaiseException,
        psycopg.errors.NoDataFound,
    ) as err:
        conn.execute(
            "UPDATE backtest_runs SET status='FAILED',finished_at=now(),result=%s WHERE id=%s",
            (Jsonb({"error": str(err).splitlines()[0][:240]}), run["id"]),
        )
        if run["account_id"]:
            conn.execute("SELECT pq_cancel(%s,'REPLAY_FAILED',now())", (run["account_id"],))
            conn.execute("UPDATE paper_accounts SET status='ERROR' WHERE id=%s", (run["account_id"],))
    return 1


def run_engine(once=False):
    stopping = False

    def stop(*_):
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    while not stopping:
        try:
            with psycopg.connect(os.environ["DATABASE_URL"], row_factory=dict_row, autocommit=True) as conn:
                if not first(conn, "SELECT pg_try_advisory_lock(608102006) ok")["ok"]:
                    raise RuntimeError("Another engine worker is active")
                with conn.transaction():
                    recovery(conn)
                while not stopping:
                    work = 0
                    ids = conn.execute("""SELECT id FROM paper_accounts a WHERE mode<>'BACKTEST' AND
                      (status IN ('RUNNING','STARTING') OR EXISTS(SELECT FROM paper_positions p
                      WHERE p.account_id=a.id AND p.status='OPEN')) ORDER BY id""").fetchall()
                    for item in ids:
                        with conn.transaction():
                            work += live_step(conn, item["id"])
                    with conn.transaction():
                        work += backtest_step(conn)
                    if once:
                        return
                    time.sleep(0.03 if work else 0.35)
        except (psycopg.Error, RuntimeError) as err:
            if once:
                raise
            LOG.error("Engine connection/recovery failed (%s): %s", type(err).__name__, str(err).splitlines()[0])
            time.sleep(3)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true")
    logging.basicConfig(level=logging.INFO)
    run_engine(parser.parse_args().once)
