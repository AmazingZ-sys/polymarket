"""Run in the candidate image against an isolated restored database."""

import json
import os
from pathlib import Path

import psycopg

from services.migrate import migrate

src = os.environ["DATABASE_URL"]
scratch = src.rsplit("/", 1)[0] + "/" + os.environ["PQ_UPGRADE_DB"]
# Role names and passwords are cluster-scoped, even when CONNECT targets a scratch DB.
for role in ("API", "ENGINE", "COLLECTOR", "OPS"):
    os.environ.pop("PQ_" + role + "_PASSWORD", None)
with psycopg.connect(scratch, autocommit=True) as conn:
    initial = dict(conn.execute("SELECT name,sha256 FROM schema_migrations"))
    files = {p.name for p in Path("database/migrations").glob("*.sql")}
    if not initial.keys() <= files:
        raise SystemExit("Candidate lacks existing migrations; use a verified rollback point.")
    migrate(scratch)
    current = dict(conn.execute("SELECT name,sha256 FROM schema_migrations"))
    added = {k: v for k, v in current.items() if k not in initial}
    if added:
        document = Path("database/rollback-compatibility.json")
        reviewed = json.loads(document.read_text()) if document.is_file() else {}
        approved = reviewed.get(os.environ["PQ_PREVIOUS_VERSION"], {})
        if any(approved.get(k) != digest for k, digest in added.items()):
            raise SystemExit("Migration set has no reviewed rollback contract for this release.")
