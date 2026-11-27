"""Reserve once, post once, and recover using read-only venue evidence."""

from __future__ import annotations

import logging
import os
import re
import time
from datetime import UTC, datetime, timedelta

import psycopg
from psycopg.rows import dict_row

from .model import Credentials, GuardError, plan_pair, prepare_signed, require, verify_market
from .venue import Venue

LOG = logging.getLogger(__name__)
# The pinned SDK maps all ambiguous/duplicate rejections to "unknown".
REJECT_CODES = {
    "unmatched",
    "market_not_ready",
    "not_enough_balance",
    "invalid_nonce",
    "invalid_expiration",
    "post_only_would_cross",
    "post_only_mode",
    "fok_not_filled",
    "fak_not_filled",
}
SIDE_NAMES = ("up", "down")


def now():
    return datetime.now(UTC)


def first(db, sql, args=()):
    return db.execute(sql, args).fetchone()


def result(db, sid, state, up=None, down=None, *accounting):
    fields = list(accounting) + [None] * (6 - len(accounting))
    db.execute("SELECT pq_live_result(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)", (sid, state, up, down, *fields))


def acknowledge(responses):
    require(isinstance(responses, (list, tuple)) and len(responses) == 2, "BATCH_RESPONSE")
    ids, rejected = [], []
    for response in responses:
        accepted = getattr(response, "ok", None) is True
        oid = getattr(response, "order_id", "") if accepted else ""
        ids.append(oid.lower() if isinstance(oid, str) and re.fullmatch(r"0x[0-9a-fA-F]{64}", oid) else None)
        rejected.append(getattr(response, "ok", None) is False and getattr(response, "code", None) in REJECT_CODES)
    require(not ids[0] or ids[0] != ids[1], "BATCH_RESPONSE")
    return tuple(ids), tuple(rejected)


def heartbeat(db, cfg, expiry=None, signer=None, reason="READY"):
    ok = reason == "READY"
    db.execute(
        """INSERT INTO polymarket_executor_health
        (singleton,revision,wallet_address,signer_address,auth_expires_at,clob_only,reconciled,geoblock_allowed,
         funds_ready,healthy,detail_code)
        VALUES(true,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        ON CONFLICT(singleton) DO UPDATE SET revision=excluded.revision,wallet_address=excluded.wallet_address,
        signer_address=excluded.signer_address,auth_expires_at=excluded.auth_expires_at,clob_only=excluded.clob_only,
        reconciled=excluded.reconciled,geoblock_allowed=excluded.geoblock_allowed,funds_ready=excluded.funds_ready,
        healthy=excluded.healthy,detail_code=excluded.detail_code,checked_at=clock_timestamp()""",
        (cfg["revision"], cfg["wallet_address"], signer, expiry, ok, ok, ok, ok, ok, reason),
    )


def settings(db):
    return first(
        db,
        """SELECT s.*, coalesce(sum(coalesce(l.up_principal,l.up_amount)+
        coalesce(l.down_principal,l.down_amount)) FILTER (WHERE l.state<>'REJECTED'),0) AS spent,
        count(l.signal_id) FILTER (WHERE l.state IN ('RESERVED','UNKNOWN','EXPOSED')) AS unresolved
        FROM polymarket_account_settings s LEFT JOIN polymarket_live_attempts l
        ON lower(l.wallet_address)=lower(s.wallet_address) GROUP BY s.singleton""",
    )


def candidate(db, cfg):
    return first(
        db,
        """SELECT signal.id,signal.created_at,signal.strategy_id,signal.action,signal.reason,
        signal.evidence,signal.strategy_version,a.config,a.strategy_id AS account_strategy,
        a.strategy_version AS account_version,m.market_id,m.slug,m.condition_id,m.asset,m.timeframe,
        m.up_token_id,m.down_token_id,m.rule_verified,m.rule_changed,m.accepting_orders,m.resolved_outcome,
        m.settlement_rule_version,m.fee_schedule_version FROM strategy_signals signal JOIN markets m USING(market_id)
        JOIN paper_accounts a ON a.id=signal.account_id
        WHERE signal.account_id=%s AND a.mode='PAPER' AND a.status='RUNNING'
        AND signal.strategy_id=%s AND signal.strategy_version=%s AND signal.config_hash=a.config_hash AND signal.id>%s
        AND signal.action='PAIR' AND signal.reason='POSITIVE_THEORETICAL_EDGE'
        AND signal.created_at BETWEEN greatest(%s, clock_timestamp()-interval '3 seconds') AND clock_timestamp()
        AND m.rule_verified AND NOT m.rule_changed AND m.accepting_orders AND m.end_time>clock_timestamp()
        AND NOT EXISTS (SELECT FROM polymarket_live_attempts l WHERE l.signal_id=signal.id)
        ORDER BY signal.id DESC LIMIT 1""",
        (
            cfg["paper_account_id"],
            cfg["strategy_id"],
            cfg["strategy_version"],
            cfg["enabled_after_signal"],
            cfg["enabled_at"],
        ),
    )


def dispatch(db, venue, cfg, row, pair):
    sid, rev = row["id"], cfg["revision"]
    up, down = pair.legs
    # autocommit makes this claim durable even if the process dies mid-request.
    if not first(db, "SELECT pq_live_reserve(%s,%s,%s,%s) ok", (rev, sid, up.principal, down.principal))["ok"]:
        return
    posted = False
    try:
        expiry, funds = venue.account(cfg["wallet_address"])
        require(funds >= pair.estimated_funds, "INSUFFICIENT_FUNDS")
        with db.transaction():
            require(
                first(db, "SELECT pq_live_dispatch_ready(%s,%s) ok", (rev, sid))["ok"], "AUTHORIZATION_CHANGED"
            )
            signed = prepare_signed(venue.client, pair, cfg["wallet_address"])
            at = now()
            require(
                at < min(expiry, pair.expires_at)
                and 0 <= (at - pair.oldest_book).total_seconds() <= 3
                and 0 <= (at - row["created_at"]).total_seconds() <= 3,
                "STALE_AT_SEND",
            )
            posted = True
            responses = venue.client.post_orders(signed)
            ids, rejected = acknowledge(responses)
            db.execute("SELECT pq_live_ack(%s,%s,%s,%s,%s)", (sid, *ids, *rejected))
            # Resolve unequivocal batch rejections without another request.
        if all(rejected):
            result(db, sid, "REJECTED", *ids, 0, 0, 0, 0, "pUSD", "pUSD")
        elif any(rejected) or not all(ids):
            # An accepted FOK is not evidence of a fill by itself.
            result(db, sid, "EXPOSED" if any(rejected) and any(ids) else "UNKNOWN", *ids)
        else:
            row.update(
                signal_id=sid,
                wallet_address=cfg["wallet_address"],
                state="RESERVED",
                up_amount=up.principal,
                down_amount=down.principal,
                up_order_id=ids[0],
                down_order_id=ids[1],
                up_principal=None,
                down_principal=None,
                updated_at=now(),
            )
            reconcile_one(db, venue.client, row, responses)
    except Exception:
        if posted:
            # A failed commit or timeout can follow a successful venue write. Do
            # not send again, even if the response could not be preserved.
            result(db, sid, "UNKNOWN")
        else:
            result(db, sid, "REJECTED", None, None, 0, 0, 0, 0, "pUSD", "pUSD")
        raise


def reconcile_one(db, client, row, responses=None):
    from .settlement import classify

    ids = tuple(row[side + "_order_id"] for side in SIDE_NAMES)
    rejected = tuple(row[side + "_principal"] == 0 and not oid for side, oid in zip(SIDE_NAMES, ids, strict=True))
    evidence = classify(client, row["wallet_address"], row, ids, rejected=rejected, responses=responses)
    if evidence[0] == "RESERVED":
        if row["state"] == "RESERVED" and now() - row["updated_at"] >= timedelta(seconds=90):
            result(db, row["signal_id"], "UNKNOWN", *ids)
        return
    if (row["state"] == "EXPOSED" and evidence[0] == "UNKNOWN") or (
        row["state"] == "CONFIRMED" and evidence[0] != "CONFIRMED"
    ):
        return
    result(db, row["signal_id"], *evidence)


def recover(db, venue):
    wallet = venue.credentials.wallet_address
    rows = db.execute(
        """SELECT l.*,m.condition_id,m.up_token_id,m.down_token_id FROM polymarket_live_attempts l
        JOIN strategy_signals s ON l.signal_id=s.id JOIN markets m USING(market_id)
        WHERE lower(l.wallet_address)=lower(%s) AND (l.state IN ('RESERVED','UNKNOWN','EXPOSED')
            OR (l.state='CONFIRMED' AND (l.up_fee IS NULL OR l.down_fee IS NULL OR l.up_principal IS NULL OR l.down_principal IS NULL)))
        AND (l.state='RESERVED' OR l.up_order_id IS NOT NULL OR l.down_order_id IS NOT NULL)
        ORDER BY (l.state='RESERVED') DESC,l.updated_at,l.signal_id LIMIT 10""",
        (wallet,),
    ).fetchall()
    if not rows:
        return
    # No identity/retry can be inferred from a reservation without durable acknowledgement.
    for row in rows:
        if row["state"] == "RESERVED" and not (row["up_order_id"] or row["down_order_id"]):
            known_rejection = row["up_principal"] == row["down_principal"] == 0
            result(db, row["signal_id"], "REJECTED" if known_rejection else "UNKNOWN")
            continue
        venue.authorize(wallet)
        reconcile_one(db, venue.client, row)


def run(db, venue):
    if not first(db, "SELECT pg_try_advisory_lock(91472031, 1) ok")["ok"]:
        return
    checked, recovered, revision = 0.0, 0.0, None
    while True:
        cfg = settings(db)
        try:
            if time.monotonic() - recovered >= 5 or recovered == 0:
                recovered = time.monotonic()
                recover(db, venue)
                cfg = settings(db)
            if not cfg["wallet_address"]:
                time.sleep(1)
                continue
            if cfg["revision"] != revision or time.monotonic() - checked >= 10:
                revision, checked = cfg["revision"], time.monotonic()
                require(cfg["strategy_id"] == "S01", "OBSERVATION_STRATEGY")
                expiry, _ = venue.account(cfg["wallet_address"])
                require(not cfg["unresolved"], "UNRECONCILED")
                heartbeat(db, cfg, expiry, venue.credentials.session_address)
            if cfg["enabled"] and cfg["geoblock_status"] == "ALLOWED" and not cfg["unresolved"]:
                row = candidate(db, cfg)
                if row is not None:
                    raw = venue.market(row)
                    market = verify_market(row, raw, now())
                    tokens = [market["up_token_id"], market["down_token_id"]]
                    books = venue.client.get_order_books(asset_ids=tokens)
                    pair = plan_pair(
                        row, market, books, now(), cfg["max_order_usd"], cfg["max_total_usd"] - cfg["spent"]
                    )
                    dispatch(db, venue, cfg, row, pair)
        except psycopg.Error:
            raise
        except Exception as error:  # noqa: BLE001 - fail closed across SDK errors
            # Disclose only known machine codes; SDK/HTTP exceptions may contain credentials.
            if cfg["wallet_address"]:
                code = str(error) if isinstance(error, GuardError) else "VERIFICATION_UNAVAILABLE"
                heartbeat(db, cfg, reason=code)
        time.sleep(0.25)


def main():
    path = os.environ.get("POLYMARKET_EXECUTOR_SECRET_FILE", "/run/secrets/polymarket_executor.json")
    while True:
        venue = None
        try:
            venue = Venue(Credentials.load(path))
            with psycopg.connect(os.environ["DATABASE_URL"], autocommit=True, row_factory=dict_row) as db:
                run(db, venue)
        except Exception:  # noqa: BLE001 - never log third-party credential errors
            LOG.error("EXECUTOR_UNAVAILABLE")
        finally:
            if venue:
                venue.close()
        time.sleep(5)
