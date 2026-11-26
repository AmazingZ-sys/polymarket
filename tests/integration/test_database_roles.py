"""Exercise the shipped role grant matrix in a new database with isolated role names."""

import os
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest
from psycopg import sql
from psycopg.conninfo import make_conninfo
from psycopg.rows import dict_row

from services.migrate import migrate
from services.ops.worker import run_job


def test_role_grants_and_private_auth_columns(monkeypatch, tmp_path):
    dsn = os.getenv("TEST_DATABASE_URL")
    if not dsn:
        pytest.skip("TEST_DATABASE_URL required for database role tests")
    suffix = uuid4().hex[:16]
    database = "pq_roles_test_" + suffix
    renamed = {name: "test_" + name + "_" + suffix for name in ("pq_collector", "pq_engine", "pq_ops", "pq_api")}
    for service in ("API", "COLLECTOR", "ENGINE", "OPS"):
        monkeypatch.delenv(f"PQ_{service}_PASSWORD", raising=False)
    monkeypatch.setenv("ARCHIVE_DIR", str(tmp_path))
    matrix = (Path(__file__).resolve().parents[2] / "database/runtime-grants.sql").read_text()
    with psycopg.connect(dsn, autocommit=True) as admin:
        admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(database)))
        try:
            for name, alias in renamed.items():
                matrix = matrix.replace(name, alias)
                admin.execute(sql.SQL("CREATE ROLE {} NOLOGIN").format(sql.Identifier(alias)))
            isolated = make_conninfo(dsn, dbname=database)
            migrate(isolated)
            with psycopg.connect(isolated, autocommit=True, row_factory=dict_row) as db:
                db.execute(matrix)
                for alias in renamed.values():
                    db.execute(sql.SQL("SET ROLE {}").format(sql.Identifier(alias)))
                    permissions = db.execute("""SELECT
                        has_column_privilege(current_user,'users','password_hash','SELECT') AS read_verifier,
                        has_schema_privilege(current_user,'public','CREATE') AS create_schema_object,
                        has_table_privilege(current_user,'schema_migrations','UPDATE') AS migrate""").fetchone()
                    assert not any(permissions.values())
                    db.execute("RESET ROLE")
                db.execute(sql.SQL("SET ROLE {}").format(sql.Identifier(renamed["pq_collector"])))
                db.execute(
                    "INSERT INTO data_source_status(source,status,last_attempt_at) VALUES ('test','TEST',now())"
                )
                with pytest.raises(psycopg.errors.InsufficientPrivilege):
                    db.execute("SELECT * FROM user_sessions")
                db.execute("RESET ROLE")
                db.execute(sql.SQL("SET ROLE {}").format(sql.Identifier(renamed["pq_api"])))
                db.execute("INSERT INTO users(username,password_hash,role) VALUES ('test','salt.test','VIEWER')")
                assert (
                    db.execute("SELECT role FROM pq_auth_identity('test','test')").fetchone()["role"] == "VIEWER"
                )
                assert db.execute("SELECT * FROM pq_auth_identity('test','wrong')").fetchone() is None
                db.execute(
                    """SELECT pq_account_command('create',NULL,'{"name":"test-account"}','role-test','admin')"""
                )
                with pytest.raises(psycopg.errors.InsufficientPrivilege):
                    db.execute("UPDATE paper_accounts SET available_balance=0")
                db.execute("RESET ROLE")
                db.execute(sql.SQL("SET ROLE {}").format(sql.Identifier(renamed["pq_ops"])))
                run_job(db, "ledger_check")
                run_job(db, "daily_report")
                assert db.execute("SELECT count(*) n FROM paper_accounts").fetchone()["n"] == 1
        finally:
            admin.execute(sql.SQL("DROP DATABASE {}").format(sql.Identifier(database)))
            for alias in renamed.values():
                admin.execute(sql.SQL("DROP ROLE IF EXISTS {}").format(sql.Identifier(alias)))
