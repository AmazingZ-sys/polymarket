"""Use an explicit test DSN and dedicated disposable databases; no network market calls."""

import json
import os
from pathlib import Path
from uuid import uuid4

import polars as pl
import pytest
from psycopg import sql
from psycopg.conninfo import make_conninfo
from psycopg.types.json import Jsonb

from pqcollector.archive import archive, checksum, compact_raw, raw_event
from pqcollector.markets import parse_market, stamp
from pqcollector.store import Store, connect
from services.migrate import migrate

ROOT = Path(__file__).resolve().parents[2]


def test_dedup_drift_recovery_and_archiving(tmp_path):
    dsn = os.getenv("TEST_DATABASE_URL")
    if not dsn:
        pytest.skip("set TEST_DATABASE_URL for PostgreSQL integration")
    temporary = "pq_collector_test_" + uuid4().hex
    with connect(dsn) as admin:
        admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(temporary)))
        try:
            scoped = make_conninfo(dsn, dbname=temporary)
            migrate(scoped)
            with connect(scoped) as conn:
                raw = json.loads((ROOT / "docs/evidence/2026-11-26/btc-updown-5m-1795648800.json").read_bytes())
                at = stamp("2026-11-26T01:25:00Z")
                store = Store(conn)
                for _ in range(2):
                    with conn.transaction():
                        store.metadata(parse_market(raw, at), at)
                assert conn.execute("SELECT count(*) AS n FROM market_resolutions").fetchone()["n"] == 1
                assert conn.execute("SELECT count(*) AS n FROM engine_events").fetchone()["n"] == 2
                raw["description"] += " Revised terms."
                with conn.transaction():
                    store.metadata(parse_market(raw, at), at)
                result = conn.execute("SELECT rule_changed,rule_verified FROM markets").fetchone()
                assert result == {"rule_changed": True, "rule_verified": False}
                raw_id = store.event("test", "RAW", None, at, at, {"test": True})
                manifests = archive(conn, tmp_path, 2)
                assert len(manifests) >= 2 and archive(conn, tmp_path) == []
                events = []
                for item in manifests:
                    file = tmp_path / item["file"]
                    assert checksum(file) == item["sha256"]
                    events.extend(pl.read_parquet(file)["seq"].to_list())
                assert events == sorted(set(events))
                assert len(events) == conn.execute("SELECT count(*) AS n FROM engine_events").fetchone()["n"]
                conn.execute("UPDATE dataset_versions SET created_at=now()-interval '2 days'")
                assert compact_raw(conn, tmp_path) == 1 and compact_raw(conn, tmp_path) == 0
                assert raw_event(conn, tmp_path, raw_id)["data"] == {"test": True}
                assert store.event("test", "RAW", None, at, at, {"test": True}) == raw_id
                assert conn.execute("SELECT count(*) n FROM engine_events WHERE kind='RAW'").fetchone()["n"] == 0
        finally:
            admin.execute(sql.SQL("DROP DATABASE {}").format(sql.Identifier(temporary)))


def test_isolated_history_round_trip_and_manifest_validation(tmp_path):
    from pqcollector.history import export, import_new, manifest_for_import

    dsn = os.getenv("TEST_DATABASE_URL")
    if not dsn:
        pytest.skip("set TEST_DATABASE_URL to run isolated PostgreSQL import")
    source = "pq_export_test_" + uuid4().hex
    target = "pq_history_test_" + uuid4().hex
    with connect(dsn) as admin:
        admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(source)))
        try:
            with connect(make_conninfo(dsn, dbname=source)) as db:
                for name in ("001_market.sql", "002_raw_archive.sql"):
                    db.execute((ROOT / "database/migrations" / name).read_text())
                at = stamp("2026-11-26T01:25:00Z")
                raw = json.loads((ROOT / "docs/evidence/2026-11-26/btc-updown-5m-1795648800.json").read_bytes())
                with db.transaction():
                    Store(db).metadata(parse_market(raw, at), at)
                bundle = export(db, tmp_path)
                original = db.execute(
                    "SELECT seq,event_id,received_at,data FROM engine_events ORDER BY seq"
                ).fetchall()
            manifest = Path(bundle["manifest"])
            with pytest.raises(ValueError, match="checksum"):
                manifest_for_import(manifest, "0" * 64)
            assert import_new(dsn, target, manifest, bundle["sha256"])["events"] == len(original)
            with pytest.raises(ValueError, match="exists"):
                import_new(dsn, target, manifest, bundle["sha256"])
            with connect(make_conninfo(dsn, dbname=target)) as db:
                assert (
                    db.execute("SELECT seq,event_id,received_at,data FROM engine_events ORDER BY seq").fetchall()
                    == original
                )
                assert db.execute("SELECT count(*) n FROM market_resolutions").fetchone()["n"] == 1
                assert db.execute("SELECT current_setting('polyquant.mode') m").fetchone()["m"] == "history"
            data = json.loads(manifest.read_bytes())
            parquet = tmp_path / data["datasets"][0]["file_path"]
            parquet.write_bytes(b"test-only-corrupt-parquet")
            with pytest.raises(ValueError, match="checksum"):
                manifest_for_import(manifest, bundle["sha256"])
        finally:
            for dbname in (source, target):
                admin.execute(sql.SQL("DROP DATABASE IF EXISTS {}").format(sql.Identifier(dbname)))


def test_backtest_dataset_request_states():
    dsn = os.getenv("TEST_DATABASE_URL")
    if not dsn:
        pytest.skip("set TEST_DATABASE_URL for dataset status integration")
    database = "pq_dataset_test_" + uuid4().hex
    with connect(dsn) as admin:
        admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(database)))
        try:
            scoped = make_conninfo(dsn, dbname=database)
            migrate(scoped)
            with connect(scoped) as db:
                start = stamp("2026-11-26T01:00:00Z")
                end = stamp("2026-11-26T01:05:00Z")
                request = db.execute(
                    """INSERT INTO backtest_datasets(assets,timeframes,start_time,end_time,requested_by)
                    VALUES(%s,%s,%s,%s,'test') RETURNING id""",
                    (["BTC"], [5], start, end),
                ).fetchone()["id"]
                row = db.execute("SELECT pq_refresh_backtest_dataset(%s) data", (request,)).fetchone()["data"]
                assert row["status"] == "DATA_UNAVAILABLE" and row["reason"] == "NO_MARKETS"
                db.execute(
                    """INSERT INTO markets(market_id,condition_id,slug,title,asset,timeframe,start_time,end_time,
                    up_token_id,down_token_id,resolution_source,settlement_rule,settlement_rule_version,
                    rule_verified,market_status,accepting_orders,raw_metadata)
                    VALUES('DATA','DATA','data','data','BTC',5,%s,%s,'u','d','test','test','r1',true,
                    'ACTIVE',true,'{}')""",
                    (start, end),
                )
                db.execute(
                    """INSERT INTO engine_events(event_id,market_id,source,kind,event_time,received_at,processed_at,data)
                    VALUES('event','DATA','test','BOOK',%s,%s,%s,'{}')""",
                    (start, start, start),
                )

                def refresh(request_id=request):
                    return db.execute("SELECT pq_refresh_backtest_dataset(%s) data", (request_id,)).fetchone()[
                        "data"
                    ]

                assert refresh()["reason"] == "MISSING_QUALIFIED_BOOK_PAIR"
                base_book = {
                    "asset": "BTC",
                    "timeframe": 5,
                    "start_time": start.isoformat(),
                    "end_time": end.isoformat(),
                    "rule_verified": True,
                    "rule_version": "r1",
                    "accepting_orders": True,
                    "tick_size": "0.01",
                    "minimum_order_size": "5",
                    "fee_rate": "0.05",
                    "fee_exponent": 1,
                    "asks": [["0.50", "20"]],
                    "bids": [["0.48", "20"]],
                }

                def add_book(side, token, flags=()):
                    db.execute(
                        """INSERT INTO engine_events(event_id,market_id,source,kind,event_time,received_at,
                        processed_at,data,quality_flags) VALUES(%s,'DATA','test','BOOK',%s,%s,%s,%s,%s)""",
                        (
                            str(uuid4()),
                            start,
                            start,
                            start,
                            Jsonb({**base_book, "side": side, "token_id": token}),
                            list(flags),
                        ),
                    )

                add_book("UP", "u")
                add_book("DOWN", "u")
                add_book("DOWN", "d", ["STALE_EVENT"])
                row = refresh()
                assert row["status"] == "DATA_INCOMPLETE" and row["book_count"] == 1
                add_book("DOWN", "d")
                row = refresh()
                assert row["status"] == "BACKTEST_READY" and row["book_market_count"] == 1
                broader = db.execute(
                    """INSERT INTO backtest_datasets(assets,timeframes,start_time,end_time,requested_by)
                    VALUES('{BTC,ETH}','{5}',%s,%s,'test') RETURNING id""",
                    (start, end),
                ).fetchone()["id"]
                assert refresh(broader)["reason"] == "MISSING_MARKET_SCOPE"
                db.execute(
                    """INSERT INTO engine_events(event_id,market_id,source,kind,event_time,received_at,processed_at,data)
                    VALUES('gap','DATA','test','GAP',%s,%s,%s,'{}')""",
                    (start, start, start),
                )
                row = db.execute("SELECT pq_refresh_backtest_dataset(%s) data", (request,)).fetchone()["data"]
                assert row["status"] == "DATA_INCOMPLETE" and row["reason"] == "MARKET_DATA_GAP"
        finally:
            admin.execute(sql.SQL("DROP DATABASE IF EXISTS {}").format(sql.Identifier(database)))
