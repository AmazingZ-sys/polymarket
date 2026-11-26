"""Use an explicit test DSN and dedicated disposable databases; no network market calls."""

import json
import os
from pathlib import Path
from uuid import uuid4

import polars as pl
import pytest
from psycopg import sql
from psycopg.conninfo import make_conninfo

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
