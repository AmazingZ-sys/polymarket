"""Use a throwaway database and a uniquely named executor role; no venue I/O."""

import os
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest
from psycopg import sql
from psycopg.conninfo import make_conninfo
from psycopg.rows import dict_row

from services.live import worker
from services.migrate import migrate


def test_guarded_claim_acknowledgement_and_revocation(monkeypatch):
    uri = os.environ.get("TEST_DATABASE_URL")
    if not uri:
        pytest.skip("TEST_DATABASE_URL required")
    for service in ("API", "COLLECTOR", "ENGINE", "OPS", "LIVE"):
        monkeypatch.delenv("PQ_" + service + "_PASSWORD", raising=False)
    suffix = uuid4().hex[:16]
    name, role = "pq_" + suffix + "_test", "pq_test_live_" + suffix
    wallet, signer = "0x" + "11" * 20, "0x" + "22" * 20
    with psycopg.connect(uri, autocommit=True) as admin:
        admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
        try:
            admin.execute(sql.SQL("CREATE ROLE {} NOLOGIN").format(sql.Identifier(role)))
            isolated = make_conninfo(uri, dbname=name)
            migrate(isolated)
            with psycopg.connect(isolated, autocommit=True, row_factory=dict_row) as db:
                live_grants = Path("database/live-grants.sql").read_text().replace("pq_live;", role + ";")
                db.execute(live_grants)
                db.execute("""INSERT INTO markets(market_id,condition_id,slug,title,asset,timeframe,start_time,end_time,
                    up_token_id,down_token_id,resolution_source,settlement_rule,settlement_rule_version,rule_verified,
                    market_status,accepting_orders,raw_metadata,created_at)
                    VALUES('1','condition','test','test','BTC',5,now()-interval '1 minute',now()+interval '4 minutes',
                    '101','102','test','test','hash',true,'ACTIVE',true,'{}',now()-interval '1 day')""")
                account = db.execute("""SELECT pq_account_command('create',NULL,
                    '{"name":"TEST","strategy_id":"S01"}','synthetic-test','test') a""").fetchone()["a"]["id"]
                db.execute("UPDATE paper_accounts SET status='RUNNING' WHERE id=%s", (account,))
                db.execute(
                    """UPDATE polymarket_account_settings AS s SET wallet_address=%s,paper_account_id=a.id,
                    strategy_id=a.strategy_id,strategy_version=a.strategy_version,max_order_usd=5,max_total_usd=8,
                    enabled=true,enabled_at=now(),attested_by='test',attested_at=now(),geoblock_status='ALLOWED'
                    FROM paper_accounts a WHERE a.id=%s""",
                    (wallet, account),
                )
                new_signal = """INSERT INTO strategy_signals(account_id,market_id,event_id,strategy_id,strategy_version,
                    config_hash,action,reason,evidence,created_at) SELECT id,'1',%s,strategy_id,strategy_version,
                    config_hash,'PAIR','POSITIVE_THEORETICAL_EDGE','{}',clock_timestamp() FROM paper_accounts WHERE id=%s
                    RETURNING id,created_at"""
                sig = db.execute(new_signal, ("one", account)).fetchone()
                db.execute(sql.SQL("SET ROLE {}").format(sql.Identifier(role)))
                cfg = worker.settings(db)
                worker.heartbeat(db, cfg, datetime.now(UTC) + timedelta(hours=1), signer)
                selected = worker.candidate(db, cfg)
                assert selected["id"] == sig["id"] and selected["created_at"] == sig["created_at"]
                sid = selected["id"]
                reserve = "SELECT pq_live_reserve(0,%s,%s,%s) ok"
                assert not worker.first(db, reserve, (sid, 6, 1))["ok"]
                assert not worker.first(db, reserve, (sid, 5, 4))["ok"]
                assert worker.first(db, reserve, (sid, 5, 3))["ok"]
                assert not worker.first(db, reserve, (sid, 1, 1))["ok"]
                assert worker.first(db, "SELECT pq_live_dispatch_ready(0,%s) ok", (sid,))["ok"]
                ack = "SELECT pq_live_ack(%s,%s,%s,%s,%s)"
                oid = "0x" + "cd" * 32
                db.execute(ack, (sid, None, oid, True, False))
                db.execute(ack, (sid, None, oid, True, False))
                with pytest.raises(psycopg.errors.RaiseException, match="Conflicting"):
                    db.execute(ack, (sid, "0x" + "ef" * 32, oid, False, False))
                assert worker.first(db, "SELECT up_principal,down_order_id FROM polymarket_live_attempts") == {
                    "up_principal": Decimal(0),
                    "down_order_id": oid,
                }
                worker.result(db, sid, "EXPOSED", None, oid)
                cfg = worker.settings(db)
                assert not cfg["enabled"] and cfg["unresolved"] == 1 and cfg["spent"] == 3
                assert not worker.first(db, "SELECT pq_live_dispatch_ready(0,%s) ok", (sid,))["ok"]
        finally:
            admin.execute(sql.SQL("DROP DATABASE {}").format(sql.Identifier(name)))
            admin.execute(sql.SQL("DROP ROLE IF EXISTS {}").format(sql.Identifier(role)))
