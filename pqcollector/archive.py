from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path

import polars as pl


def checksum(path: Path) -> str:
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def archive(db, directory: Path, batch_size: int = 5000) -> list[dict]:
    if not 1 <= batch_size <= 10000:
        raise ValueError("batch size outside 1..10000")
    directory.mkdir(parents=True, exist_ok=True)
    results: list[dict] = []
    if not db.execute("SELECT pg_try_advisory_lock(7386102) AS ok").fetchone()["ok"]:
        raise RuntimeError("Another archive writer is active")
    try:
        watermark = db.execute("SELECT coalesce(max(seq),0) AS seq FROM engine_events").fetchone()["seq"]
        while True:
            with db.transaction():
                cursor = db.execute("SELECT coalesce(max(last_seq),0) AS seq FROM dataset_versions").fetchone()[
                    "seq"
                ]
                rows = db.execute(
                    """
                    SELECT seq,event_id,market_id,source,kind,event_time,received_at,processed_at,
                        persisted_at,sequence,data::text AS data,to_json(quality_flags)::text AS quality_flags
                    FROM engine_events WHERE seq>%s AND seq<=%s ORDER BY seq LIMIT %s
                    """,
                    (cursor, watermark, batch_size),
                ).fetchall()
                if not rows:
                    compact_raw(db, directory)
                    return results
                first, last = rows[0]["seq"], rows[-1]["seq"]
                key = f"events-{first:020d}-{last:020d}"
                file = directory / (key + ".parquet")
                with tempfile.NamedTemporaryFile(dir=directory, suffix=".partial", delete=False) as tmp:
                    temp = Path(tmp.name)
                try:
                    frame = pl.DataFrame(rows, schema_overrides={"market_id": pl.String, "sequence": pl.Int64})
                    frame.write_parquet(temp, compression="zstd")
                    sha = checksum(temp)
                    if file.exists():
                        if checksum(file) != sha:
                            raise RuntimeError("Archive range already exists with a different hash")
                    else:
                        with temp.open("rb") as handle:
                            os.fsync(handle.fileno())
                        os.link(temp, file)
                        fd = os.open(directory, os.O_RDONLY)
                        try:
                            os.fsync(fd)
                        finally:
                            os.close(fd)
                    db.execute(
                        """
                        INSERT INTO dataset_versions(version,file_path,sha256,first_seq,last_seq,event_count)
                        VALUES(%s,%s,%s,%s,%s,%s)
                        """,
                        (key, file.name, sha, first, last, len(rows)),
                    )
                    results.append({"version": key, "sha256": sha, "count": len(rows), "file": file.name})
                finally:
                    temp.unlink(missing_ok=True)
    finally:
        db.execute("SELECT pg_advisory_unlock(7386102)")
    return results


def compact_raw(db, directory: Path, *, retention_hours: int = 24, limit: int = 32) -> int:
    """Move only older RAW packets to their verified immutable archive."""
    if retention_hours < 1 or not 1 <= limit <= 256:
        raise ValueError("Invalid compaction limits")
    moved = 0
    files = db.execute(
        """
       SELECT version,file_path,sha256,first_seq,last_seq,event_count FROM dataset_versions
       WHERE raw_compacted_at IS NULL AND created_at<now()-(%s*interval '1 hour')
       ORDER BY first_seq LIMIT %s
       """,
        (retention_hours, limit),
    ).fetchall()
    for dataset in files:
        file = directory / dataset["file_path"]
        if file.parent != directory or checksum(file) != dataset["sha256"]:
            raise RuntimeError("Checksum mismatch, keeping hot observations")
        raw = pl.scan_parquet(file).filter(pl.col("kind") == "RAW").select("event_id", "seq").collect()
        with db.transaction():
            rows = db.execute(
                """SELECT event_id,seq FROM engine_events WHERE seq BETWEEN %s AND %s AND kind='RAW'
                ORDER BY seq""",
                (dataset["first_seq"], dataset["last_seq"]),
            ).fetchall()
            ids = [(r["event_id"], r["seq"]) for r in rows]
            if ids != list(raw.iter_rows()):
                raise RuntimeError("Archive event identities differ, keeping hot observations")
            db.execute("SELECT pq_archive_raw(%s)", (dataset["version"],))
            moved += len(ids)
    return moved


def raw_event(db, directory: Path, event_id: str):
    hot = db.execute("SELECT * FROM engine_events WHERE event_id=%s AND kind='RAW'", (event_id,)).fetchone()
    if hot:
        return hot
    cold = db.execute(
        """SELECT a.seq,d.file_path,d.sha256 FROM archived_raw_events a JOIN dataset_versions d
        ON d.version=a.dataset_version WHERE a.event_id=%s""",
        (event_id,),
    ).fetchone()
    if not cold:
        return None
    file = directory / cold["file_path"]
    if file.parent != directory or checksum(file) != cold["sha256"]:
        raise RuntimeError("Archived source verification failed")
    event = (
        pl.scan_parquet(file)
        .filter((pl.col("seq") == cold["seq"]) & (pl.col("event_id") == event_id))
        .collect()
        .row(0, named=True)
    )
    event["data"] = json.loads(event["data"])
    event["quality_flags"] = json.loads(event["quality_flags"])
    return event
