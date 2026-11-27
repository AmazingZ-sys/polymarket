"""Apply ordered, immutable SQL migrations under one PostgreSQL session lock."""

import hashlib
import os
from pathlib import Path

import psycopg
from psycopg import sql

ROOT = Path(__file__).resolve().parents[1]


def provision_roles(conn) -> None:
    names = ("api", "collector", "engine", "ops")
    secrets = {name: os.environ.get(f"PQ_{name.upper()}_PASSWORD") for name in names}
    if not any(secrets.values()):
        return
    if not all(secrets.values()):
        raise ValueError("Supply credentials for all four database services")
    if live_password := os.environ.get("PQ_LIVE_PASSWORD"):
        secrets["live"] = live_password
    database = conn.execute("SELECT current_database()").fetchone()[0]
    with conn.transaction():
        conn.execute("SELECT pg_advisory_xact_lock(2026112601)")
        # PostgreSQL's password encryption generates SCRAM verifiers. Send only
        # a verifier in role-management SQL, so statement logs omit secrets.
        for name, password in secrets.items():
            if password is None:
                raise ValueError("Missing runtime database credential")
            role = "pq_" + name
            if not conn.execute("SELECT 1 FROM pg_roles WHERE rolname=%s", (role,)).fetchone():
                conn.execute(
                    sql.SQL(
                        "CREATE ROLE {} LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS"
                    ).format(sql.Identifier(role))
                )
            verifier = conn.pgconn.encrypt_password(password.encode(), role.encode(), b"scram-sha-256").decode()
            conn.execute(sql.SQL("ALTER ROLE {} PASSWORD {}").format(sql.Identifier(role), sql.Literal(verifier)))
            conn.execute(
                sql.SQL("ALTER ROLE {} IN DATABASE {} SET search_path=public,pg_catalog").format(
                    sql.Identifier(role), sql.Identifier(database)
                )
            )
            conn.execute(
                sql.SQL("GRANT CONNECT ON DATABASE {} TO {}").format(
                    sql.Identifier(database), sql.Identifier(role)
                )
            )
        conn.execute(sql.SQL("REVOKE ALL ON DATABASE {} FROM PUBLIC").format(sql.Identifier(database)))
        conn.execute((ROOT / "database/runtime-grants.sql").read_text())
        if "live" in secrets:
            conn.execute((ROOT / "database/live-grants.sql").read_text())


def migrate(dsn: str) -> None:
    with psycopg.connect(dsn, autocommit=True) as conn:
        conn.execute("SELECT pg_advisory_lock(2026112600)")
        conn.execute("""CREATE TABLE IF NOT EXISTS schema_migrations (
            name text PRIMARY KEY, sha256 text NOT NULL, applied_at timestamptz NOT NULL DEFAULT now())""")
        for file in sorted((ROOT / "database/migrations").glob("*.sql")):
            raw = file.read_bytes()
            digest = hashlib.sha256(raw).hexdigest()
            row = conn.execute("SELECT sha256 FROM schema_migrations WHERE name=%s", (file.name,)).fetchone()
            if row:
                if row[0] != digest:
                    raise ValueError(f"applied migration changed: {file.name}")
                continue
            with conn.transaction():
                conn.execute(raw.decode())
                conn.execute("INSERT INTO schema_migrations(name,sha256) VALUES(%s,%s)", (file.name, digest))
            print(f"applied {file.name}", flush=True)
        provision_roles(conn)


if __name__ == "__main__":
    migrate(os.environ["DATABASE_URL"])
