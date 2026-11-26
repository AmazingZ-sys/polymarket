"""Outbox retries, scheduling and clean reports on a disposable Postgres schema."""

import os
from pathlib import Path
from uuid import uuid4

import httpx
import psycopg
import pytest
from psycopg import sql
from psycopg.rows import dict_row

from services.ops import worker


@pytest.fixture
def ops_db():
    dsn = os.getenv("TEST_DATABASE_URL")
    if not dsn:
        pytest.skip("set TEST_DATABASE_URL for PostgreSQL integration")
    with psycopg.connect(dsn, row_factory=dict_row) as db:
        schema = sql.Identifier("test_ops_" + uuid4().hex)
        db.execute(sql.SQL("CREATE SCHEMA {}").format(schema))
        db.execute(sql.SQL("SET LOCAL search_path TO {}").format(schema))
        for path in sorted(Path("database/migrations").glob("*.sql")):
            # The runtime-role provisioner is exercised by the deployment test in its own database.
            db.execute(path.read_text())
        yield db
        db.rollback()


def test_outbox_dedup_and_retry(ops_db, monkeypatch):
    monkeypatch.setenv("WEBHOOK_URL", "https://fixture.invalid/test")
    for _ in range(2):
        worker.notice(ops_db, "TEST/event", "synthetic test")

    def offline(*args, **kwargs):
        raise httpx.ConnectTimeout("fixture")

    monkeypatch.setattr(httpx, "post", offline)
    worker.send_one(ops_db)
    assert ops_db.execute("SELECT status,attempts FROM notification_logs").fetchall() == [
        {"status": "PENDING", "attempts": 1}
    ]
    ops_db.execute("UPDATE notification_logs SET next_attempt_at=now()")
    monkeypatch.setattr(
        httpx,
        "post",
        lambda *args, **kwargs: httpx.Response(200, request=httpx.Request("POST", "https://fixture.invalid/")),
    )
    worker.send_one(ops_db)
    assert ops_db.execute("SELECT status,attempts FROM notification_logs").fetchall() == [
        {"status": "SENT", "attempts": 2}
    ]


def test_reports_recoverable_and_missing_metrics(ops_db):
    for _ in range(2):
        worker.run_job(ops_db, "daily_report")
    rows = ops_db.execute("SELECT result FROM performance_reports").fetchall()
    assert len(rows) == 1
    assert rows[0]["result"]["accounts"] == 0
    assert rows[0]["result"]["win_rate"] is None
    assert rows[0]["result"]["profit_factor"] is None
    ops_db.execute("UPDATE scheduled_jobs SET next_run_at=now()+interval '1 hour'")
    ops_db.execute("UPDATE scheduled_jobs SET next_run_at=now() WHERE name='ledger_check'")
    worker.once(ops_db)
    result = ops_db.execute(
        "SELECT last_result,next_run_at>now() scheduled FROM scheduled_jobs WHERE name='ledger_check'"
    ).fetchone()
    assert result == {"last_result": "OK", "scheduled": True}
