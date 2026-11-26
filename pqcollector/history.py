"""Content-verified, data-only export and restore to a new BACKTEST database."""

from __future__ import annotations

import gzip
import hashlib
import json
import os
import re
import tempfile
from pathlib import Path

import polars as pl
from psycopg import sql
from psycopg.conninfo import make_conninfo
from psycopg.types.json import Jsonb

from services.migrate import migrate

from .archive import archive, checksum
from .store import connect

TABLES = ("markets", "market_rule_versions", "fee_schedules", "market_resolutions")
EVENT_COLUMNS = (
    "seq",
    "event_id",
    "market_id",
    "source",
    "kind",
    "event_time",
    "received_at",
    "processed_at",
    "persisted_at",
    "sequence",
    "data",
    "quality_flags",
)
SHA256 = re.compile(r"[0-9a-f]{64}")
MIGRATIONS = Path(__file__).resolve().parents[1] / "database/migrations"


def publish(tmp: Path, result: Path):
    try:
        os.link(tmp, result)
    except FileExistsError:
        if checksum(tmp) != checksum(result):
            raise ValueError("Immutable bundle file exists with different contents")


def export(db, directory: Path) -> dict:
    directory.mkdir(parents=True, exist_ok=True)
    with db.transaction():
        db.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
        archive(db, directory)
        datasets = db.execute("""SELECT version,file_path,sha256,first_seq,last_seq,event_count FROM dataset_versions
            ORDER BY first_seq""").fetchall()
        if not datasets:
            raise ValueError("No observed events to export")
        refs = {}
        for table in TABLES:
            with tempfile.NamedTemporaryFile(dir=directory, delete=False) as tmp:
                path = Path(tmp.name)
                try:
                    with (
                        db.cursor(name="export_" + table) as cursor,
                        gzip.GzipFile(filename="", mode="wb", fileobj=tmp, mtime=0) as stream,
                    ):
                        cursor.execute(
                            sql.SQL("SELECT row_to_json(t)::text AS row FROM {} t ORDER BY 1").format(
                                sql.Identifier(table)
                            )
                        )
                        count = 0
                        for record in cursor:
                            stream.write(record["row"].encode() + b"\n")
                            count += 1
                    tmp.flush()
                    os.fsync(tmp.fileno())
                    digest = checksum(path)
                    target = directory / f"{table}-{digest}.jsonl.gz"
                    publish(path, target)
                    refs[table] = {"file": target.name, "sha256": digest, "count": count}
                finally:
                    path.unlink(missing_ok=True)
        content = {
            "format": "polyquant-history-v1",
            "datasets": datasets,
            "references": refs,
            "market_schema": {n: checksum(MIGRATIONS / n) for n in ("001_market.sql", "002_raw_archive.sql")},
        }
        body = (json.dumps(content, sort_keys=True, indent=2) + "\n").encode()
        digest = hashlib.sha256(body).hexdigest()
        name = f"manifest-{digest}.json"
        with tempfile.NamedTemporaryFile(dir=directory, delete=False) as tmp:
            path = Path(tmp.name)
            try:
                tmp.write(body)
                tmp.flush()
                os.fsync(tmp.fileno())
                publish(path, directory / name)
            finally:
                path.unlink(missing_ok=True)
    return {
        "manifest": str(directory / name),
        "sha256": digest,
        "event_count": sum(d["event_count"] for d in datasets),
    }


def checked_file(directory: Path, name: str, digest: str) -> Path:
    path = directory / name
    if not SHA256.fullmatch(digest) or name != Path(name).name or path.resolve().parent != directory.resolve():
        raise ValueError("Invalid bundle path or digest")
    if checksum(path) != digest:
        raise ValueError("Bundle checksum mismatch")
    return path


def manifest_for_import(manifest: Path, expected: str) -> dict:
    checked_file(manifest.parent, manifest.name, expected)
    data = json.loads(manifest.read_bytes())
    if data.get("format") != "polyquant-history-v1" or set(data.get("references", {})) != set(TABLES):
        raise ValueError("Unsupported history manifest")
    required = {n: checksum(MIGRATIONS / n) for n in ("001_market.sql", "002_raw_archive.sql")}
    if data.get("market_schema") != required:
        raise ValueError("Incompatible market schema")
    if not data.get("datasets"):
        raise ValueError("Empty history manifest")
    for info in data["references"].values():
        checked_file(manifest.parent, info["file"], info["sha256"])
    last = 0
    for item in data["datasets"]:
        checked_file(manifest.parent, item["file_path"], item["sha256"])
        if not last < item["first_seq"] <= item["last_seq"] or item["event_count"] < 1:
            raise ValueError("Invalid or overlapping archive boundaries")
        last = item["last_seq"]
    return data


def load_table(db, table: str, info: dict, directory: Path):
    path = checked_file(directory, info["file"], info["sha256"])
    query = sql.SQL("INSERT INTO {} SELECT * FROM json_populate_record(NULL::{},%s::json)").format(
        sql.Identifier(table), sql.Identifier(table)
    )
    with gzip.open(path, "rt") as handle, db.cursor() as cursor:
        count = 0
        batch = []
        for line in handle:
            batch.append((line,))
            if len(batch) == 500:
                cursor.executemany(query, batch)
                count += len(batch)
                batch = []
        cursor.executemany(query, batch)
        count += len(batch)
        if count != info["count"]:
            raise ValueError("Metadata count mismatch")


def restore_events(db, data, directory):
    total = 0
    for item in data["datasets"]:
        path = checked_file(directory, item["file_path"], item["sha256"])
        frame = pl.read_parquet(path)
        if tuple(frame.columns) != EVENT_COLUMNS or frame.height != item["event_count"]:
            raise ValueError("Event schema or count mismatch")
        ids = frame["event_id"].to_list()
        seq = frame["seq"].to_list()
        if (
            seq != sorted(set(seq))
            or not seq
            or seq[0] != item["first_seq"]
            or seq[-1] != item["last_seq"]
            or len(set(ids)) != len(ids)
            or not all(SHA256.fullmatch(i) for i in ids)
        ):
            raise ValueError("Event ID or sequence mismatch")
        with db.cursor().copy("COPY engine_events (" + ",".join(EVENT_COLUMNS) + ") FROM STDIN") as writer:
            for row in frame.iter_rows():
                writer.write_row((*row[:-2], Jsonb(json.loads(row[-2])), json.loads(row[-1])))
        db.execute(
            """INSERT INTO dataset_versions(version,file_path,sha256,first_seq,last_seq,event_count)
            VALUES(%(version)s,%(file_path)s,%(sha256)s,%(first_seq)s,%(last_seq)s,%(event_count)s)""",
            item,
        )
        total += frame.height
    db.execute(
        "SELECT setval(pg_get_serial_sequence('engine_events','seq'),(SELECT max(seq) FROM engine_events),true)"
    )
    return total


def import_new(admin_dsn: str, name: str, manifest: Path, expected: str) -> dict:
    if not re.fullmatch(r"pq_history_[a-z0-9_]{1,48}", name):
        raise ValueError("Choose a new pq_history_ database name")
    data = manifest_for_import(manifest, expected)
    # CREATE DATABASE has no IF NOT EXISTS, making an accidental merge impossible.
    with connect(admin_dsn) as admin:
        admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
        admin.execute(sql.SQL("ALTER DATABASE {} SET polyquant.mode='history'").format(sql.Identifier(name)))
    dsn = make_conninfo(admin_dsn, dbname=name)
    migrate(dsn)
    with connect(dsn) as db, db.transaction():
        for table in TABLES[:-1]:
            load_table(db, table, data["references"][table], manifest.parent)
        total = restore_events(db, data, manifest.parent)
        load_table(db, "market_resolutions", data["references"]["market_resolutions"], manifest.parent)
        db.execute("""CREATE TABLE imported_history (sha256 text PRIMARY KEY, event_count bigint NOT NULL,
            imported_at timestamptz NOT NULL DEFAULT now())""")
        db.execute("INSERT INTO imported_history(sha256,event_count) VALUES(%s,%s)", (expected, total))
        db.execute("ALTER TABLE paper_accounts ADD CONSTRAINT historical_accounts_only CHECK(mode='BACKTEST')")
    return {"database": name, "sha256": expected, "events": total}
