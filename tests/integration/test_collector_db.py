"""Use an explicit test DSN and a unique schema; no network market calls."""

import json
import os
from pathlib import Path
from uuid import uuid4

import polars as pl
import pytest
from psycopg import sql

from pqcollector.archive import archive, checksum, compact_raw, raw_event
from pqcollector.markets import parse_market, stamp
from pqcollector.store import Store, connect

ROOT = Path(__file__).resolve().parents[2]


def test_dedup_drift_recovery_and_archiving(tmp_path):
    dsn = os.getenv("TEST_DATABASE_URL")
    if not dsn:
        pytest.skip("set TEST_DATABASE_URL for PostgreSQL integration")
    schema = "pq_collector_test_" + uuid4().hex
    with connect(dsn) as conn:
        conn.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
        try:
            conn.execute(sql.SQL("SET search_path TO {}").format(sql.Identifier(schema)))
            for file in ("001_market.sql", "002_raw_archive.sql"):
                conn.execute((ROOT / "database/migrations" / file).read_text())
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
            conn.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))
