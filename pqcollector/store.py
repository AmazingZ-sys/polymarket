from __future__ import annotations

import os
import shutil
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
        self.persisted_bytes = 0
        self.daily_event_budget_bytes = 0
        self.budget_notice_emitted = False
        self.budget_day = now().date()
        self.min_free_bytes = 0
        self.max_disk_usage_bytes = 0
        self.disk_guard_triggered = False
        self.disk_guard_reason = "DISK_FREE_LOW"
        self.last_disk_check = 0.0
        self.deduplicate_metadata = False

    def configure_budget(
        self,
        daily_event_budget_bytes: int,
        min_free_bytes: int = 0,
        deduplicate_metadata: bool = False,
        max_disk_usage_bytes: int = 0,
    ):
        self.daily_event_budget_bytes = daily_event_budget_bytes
        self.min_free_bytes = min_free_bytes
        self.deduplicate_metadata = deduplicate_metadata
        self.max_disk_usage_bytes = max_disk_usage_bytes
        if daily_event_budget_bytes:
            row = self.db.execute(
                "SELECT coalesce(sum(octet_length(data::text)), 0) AS bytes "
                "FROM engine_events WHERE persisted_at >= date_trunc('day', now())"
            ).fetchone()
            self.persisted_bytes = int(row["bytes"] if isinstance(row, dict) else row[0])

    def _disk_has_room(self) -> bool:
        if self.min_free_bytes <= 0 or monotonic() - self.last_disk_check < 10:
            return not self.disk_guard_triggered
        self.last_disk_check = monotonic()
        try:
            usage = shutil.disk_usage(os.getenv("ARCHIVE_DIR", "/tmp"))
        except OSError:
            return True
        self.disk_guard_reason = "DISK_FREE_LOW"
        if self.max_disk_usage_bytes and usage.used >= self.max_disk_usage_bytes:
            self.disk_guard_reason = "DISK_USAGE_LIMIT"
        self.disk_guard_triggered = bool(
            (self.min_free_bytes and usage.free < self.min_free_bytes)
            or (self.max_disk_usage_bytes and usage.used >= self.max_disk_usage_bytes)
        )
        return not self.disk_guard_triggered

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
        persist: bool = True,
    ) -> str:
        key = digest([source, kind, market_id, identity if identity is not None else [at, data]])
        if not persist:
            return key
        current_day = now().date()
        if current_day != self.budget_day:
            self.persisted_bytes = 0
            self.budget_notice_emitted = False
            self.budget_day = current_day
        # Estimate before enqueueing. A hard budget protects a small disk even
        # when a source suddenly becomes noisy. Essential event types remain
        # writable so gaps and settlement evidence are never silently dropped.
        estimate = len(str(data).encode()) + 256
        essential = kind in {"GAP", "RESOLUTION", "METADATA", "TRADE"}
        if not essential and not self._disk_has_room():
            if not self.budget_notice_emitted:
                self.budget_notice_emitted = True
                self.gap(
                    market_id,
                    self.disk_guard_reason,
                    source,
                    {
                        "min_free_bytes": self.min_free_bytes,
                        "max_disk_usage_bytes": self.max_disk_usage_bytes,
                    },
                )
            return key
        if self.daily_event_budget_bytes and self.persisted_bytes + estimate > self.daily_event_budget_bytes and not essential:
            if not self.budget_notice_emitted:
                self.budget_notice_emitted = True
                self.gap(market_id, "RESOURCE_BUDGET_EXCEEDED", source, {"budget_bytes": self.daily_event_budget_bytes})
            return key
        self.persisted_bytes += estimate
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
        metadata_identity = m["raw"]
        if self.deduplicate_metadata:
            metadata_identity = {
                "rule_version": m["rule_version"],
                "rule_verified": m["rule_verified"],
                "market_status": m["market_status"],
                "accepting_orders": m["accepting_orders"],
                "reference_price": m["reference_price"],
                "outcome": m["outcome"],
                "tick_size": m["tick_size"],
                "minimum_order_size": m["minimum_order_size"],
            }
        self.event("gamma", "METADATA", mid, received, received, m["raw"], flags, identity=metadata_identity)
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

    def unsettled(self, limit: int = 8) -> list[str]:
        rows = self.db.execute("""
            SELECT slug FROM markets WHERE resolved_outcome IS NULL AND end_time<now()
            AND market_status <> 'ANOMALY' ORDER BY updated_at LIMIT %s
        """, (limit,)).fetchall()
        return [r["slug"] for r in rows]


def connect(dsn: str):
    return psycopg.connect(dsn, row_factory=dict_row, autocommit=True, connect_timeout=8)
