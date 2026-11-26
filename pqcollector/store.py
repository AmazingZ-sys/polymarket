from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime
from time import monotonic
from typing import Any

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from .markets import digest, now

EVENT_INSERT = """INSERT INTO engine_events(event_id,source,kind,market_id,event_time,received_at,
    processed_at,data,quality_flags,sequence) SELECT %s,%s,%s,%s,%s,%s,%s,%s,%s,%s
    WHERE %s::text <> 'RAW' OR NOT EXISTS (SELECT 1 FROM archived_raw_events WHERE event_id=%s)
    ON CONFLICT(event_id) DO NOTHING"""


class Store:
    def __init__(self, db: psycopg.Connection[dict[str, Any]]):
        self.db = db
        self.pending: list[tuple] = []
        self.buffering = False
        self.last_flush = monotonic()

    @contextmanager
    def frame(self):
        # No await is allowed inside this scope. Multiple channel readers share
        # the single writer in arrival order, flushing complete frames together.
        self.buffering = True
        try:
            yield
        finally:
            self.buffering = False
            if len(self.pending) >= 64 or monotonic() - self.last_flush >= 0.1:
                self.flush()

    def flush(self):
        if self.pending:
            with self.db.transaction(), self.db.cursor() as cursor:
                cursor.executemany(EVENT_INSERT, self.pending)
            self.pending.clear()
        self.last_flush = monotonic()

    def event(
        self,
        source: str,
        kind: str,
        market_id: str | None,
        at: datetime,
        received: datetime,
        data: dict,
        flags=(),
        identity: Any = None,
        sequence: int | None = None,
    ) -> str:
        key = digest([source, kind, market_id, identity if identity is not None else [at, data]])
        values = (key, source, kind, market_id, at, received, now(), Jsonb(data), list(flags), sequence, kind, key)
        if self.buffering:
            self.pending.append(values)
        else:
            self.flush()
            self.db.execute(EVENT_INSERT, values)
        return key

    def status(self, source: str, status: str, reason: str | None = None):
        if not self.buffering:
            self.flush()
        at = now()
        self.db.execute(
            """
            INSERT INTO data_source_status VALUES (%s,%s,%s,%s,%s)
            ON CONFLICT(source) DO UPDATE SET status=excluded.status,last_attempt_at=excluded.last_attempt_at,
                last_success_at=coalesce(excluded.last_success_at,data_source_status.last_success_at),
                last_error=excluded.last_error
            """,
            (source, status, at, at if status == "ONLINE" else None, reason),
        )

    def gap(self, market_id: str | None, reason: str, source="clob_ws", raw=None, received=None):
        at = received or now()
        self.event(source, "GAP", market_id, at, at, {"reason": reason, "raw": raw}, [reason])

    def metadata(self, m: dict, received: datetime) -> dict:
        mid = m["market_id"]
        previous = self.db.execute(
            """
            SELECT settlement_rule_version,rule_changed,resolved_outcome,market_status
            FROM markets WHERE market_id=%s FOR UPDATE
            """,
            (mid,),
        ).fetchone()
        old_resolution = previous["resolved_outcome"] if previous else None
        if previous and (previous["rule_changed"] or previous["settlement_rule_version"] != m["rule_version"]):
            m["rule_verified"] = False
            if not previous["rule_changed"]:
                self.gap(mid, "RULE_CHANGED", "gamma", received=received)
        changed = bool(
            previous and (previous["rule_changed"] or previous["settlement_rule_version"] != m["rule_version"])
        )
        if previous and old_resolution and old_resolution != m["outcome"]:
            if previous["market_status"] != "ANOMALY":
                self.gap(mid, "RESOLUTION_CONFLICT", "gamma", received=received)
            m.update(market_status="ANOMALY", rule_verified=False, accepting_orders=False)
        outcome = m["outcome"] if m["rule_verified"] else None
        if m["outcome"] and not outcome:
            m["market_status"] = "ANOMALY"
        values = {
            k: m[k]
            for k in (
                "market_id",
                "condition_id",
                "slug",
                "title",
                "asset",
                "timeframe",
                "start_time",
                "end_time",
                "up_token_id",
                "down_token_id",
                "reference_price",
                "resolution_source",
                "settlement_rule",
                "rule_verified",
                "twap_window",
                "fee_schedule_version",
                "tick_size",
                "minimum_order_size",
                "market_status",
                "accepting_orders",
            )
        }
        values.update(
            raw_metadata=Jsonb(m["raw"]),
            settlement_rule_version=m["rule_version"],
            reference_price_source="gamma:eventMetadata.priceToBeat" if m["reference_price"] else None,
            resolved_outcome=old_resolution or outcome,
            rule_changed=changed,
        )
        cols = list(values)
        updates = ",".join(f"{c}=excluded.{c}" for c in cols if c != "market_id")
        self.db.execute(
            f"INSERT INTO markets ({','.join(cols)}) VALUES ({','.join(['%s'] * len(cols))}) "
            f"ON CONFLICT(market_id) DO UPDATE SET {updates},updated_at=clock_timestamp()",
            tuple(values.values()),
        )
        self.db.execute(
            "INSERT INTO market_rule_versions(market_id,version,snapshot) VALUES (%s,%s,%s) ON CONFLICT DO NOTHING",
            (mid, m["rule_version"], Jsonb(m["rule_fields"])),
        )
        self.db.execute(
            "INSERT INTO fee_schedules(version,schedule) VALUES (%s,%s) ON CONFLICT DO NOTHING",
            (m["fee_schedule_version"], Jsonb(m["fee_schedule"])),
        )
        flags = []
        if not m["rule_verified"]:
            flags.append("RULE_UNVERIFIED")
        if m["fee_rate"] is None:
            flags.append("FEE_UNVERIFIED")
        if m["market_status"] == "ANOMALY":
            flags.append("MARKET_ANOMALY")
        self.event("gamma", "METADATA", mid, received, received, m["raw"], flags, identity=m["raw"])
        if outcome:
            details = {
                "verified": True,
                "outcome": outcome,
                "rule_version": m["rule_version"],
                "source": "gamma:resolved+outcomePrices+eventMetadata",
                "raw": m["raw"],
            }
            key = self.event(
                "gamma", "RESOLUTION", mid, received, received, details, identity=[m["rule_version"], outcome]
            )
            self.db.execute(
                """
                INSERT INTO market_resolutions(market_id,outcome,rule_version,source,event_id,received_at)
                VALUES (%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING
                """,
                (mid, outcome, m["rule_version"], details["source"], key, received),
            )
        m["observed_at"] = received
        return m

    def unsettled(self) -> list[str]:
        rows = self.db.execute("""
            SELECT slug FROM markets WHERE resolved_outcome IS NULL AND end_time<now()
            AND market_status <> 'ANOMALY' ORDER BY updated_at LIMIT 8
        """).fetchall()
        return [r["slug"] for r in rows]


def connect(dsn: str):
    return psycopg.connect(dsn, row_factory=dict_row, autocommit=True, connect_timeout=8)
