"""Validated resource profiles for the public market collector."""

from __future__ import annotations

import os
from dataclasses import dataclass


def _int(name: str, raw: str, minimum: int = 1) -> int:
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer") from exc
    if value < minimum:
        raise ValueError(f"{name} must be at least {minimum}")
    return value


def _float(name: str, raw: str, minimum: float = 0.0) -> float:
    try:
        value = float(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be a number") from exc
    if value < minimum:
        raise ValueError(f"{name} must be at least {minimum}")
    return value


def _bool(name: str, raw: str) -> bool:
    value = raw.strip().lower()
    if value in {"1", "true", "yes", "on"}:
        return True
    if value in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"{name} must be a boolean")


def _env(name: str, default: object) -> str:
    """Treat an empty Compose environment value as unset."""

    value = os.getenv(name)
    return str(default) if value is None or not value.strip() else value


@dataclass(frozen=True)
class CollectorConfig:
    profile: str
    discovery_interval_seconds: int
    unsettled_limit: int
    market_offsets: tuple[int, ...]
    raw_sample_seconds: float
    reference_interval_seconds: int
    enable_reference_trades: bool
    daily_event_budget_bytes: int
    book_publish_interval_seconds: float
    book_tail_interval_seconds: float
    book_tail_seconds: int
    min_free_bytes: int
    max_disk_usage_bytes: int
    deduplicate_metadata: bool

    @classmethod
    def from_env(cls) -> CollectorConfig:
        profile = os.getenv("COLLECTOR_PROFILE", "full").strip().lower()
        if profile not in {"full", "free"}:
            raise ValueError("COLLECTOR_PROFILE must be full or free")
        defaults = {
            "discovery": 60 if profile == "free" else 20,
            "unsettled": 4 if profile == "free" else 8,
            "offsets": (0,) if profile == "free" else (0, 1, -1, -2),
            "raw_sample": 5.0 if profile == "free" else 0.0,
            "reference": 30 if profile == "free" else 10,
            "trades": profile == "full",
            "book_interval": 2.0 if profile == "free" else 0.25,
            "book_tail_interval": 0.25,
            "book_tail_seconds": 60,
            # 15 GiB is a disk-wide operational limit. This event budget is a
            # conservative daily guardrail and can be lowered by deployment.
            # Keep the live PostgreSQL payload small enough to leave room for
            # indexes, WAL, Docker layers, and a recovery copy on a 15 GiB VM.
            "daily_budget": 30_000_000 if profile == "free" else 0,
            "min_free_bytes": 2 * 1024**3 if profile == "free" else 0,
            "max_disk_usage_bytes": 15 * 1024**3 if profile == "free" else 0,
        }
        enable_trades = defaults["trades"]
        if os.getenv("COLLECTOR_ENABLE_REFERENCE_TRADES", "").strip():
            enable_trades = _bool("COLLECTOR_ENABLE_REFERENCE_TRADES", os.environ["COLLECTOR_ENABLE_REFERENCE_TRADES"])
        return cls(
            profile=profile,
            discovery_interval_seconds=_int(
                "COLLECTOR_DISCOVERY_INTERVAL_SECONDS",
                _env("COLLECTOR_DISCOVERY_INTERVAL_SECONDS", defaults["discovery"]),
            ),
            unsettled_limit=_int(
                "COLLECTOR_UNSETTLED_LIMIT",
                _env("COLLECTOR_UNSETTLED_LIMIT", defaults["unsettled"]),
            ),
            market_offsets=defaults["offsets"],
            raw_sample_seconds=_float(
                "COLLECTOR_RAW_SAMPLE_SECONDS",
                _env("COLLECTOR_RAW_SAMPLE_SECONDS", defaults["raw_sample"]),
            ),
            reference_interval_seconds=_int(
                "COLLECTOR_REFERENCE_INTERVAL_SECONDS",
                _env("COLLECTOR_REFERENCE_INTERVAL_SECONDS", defaults["reference"]),
            ),
            enable_reference_trades=enable_trades,
            daily_event_budget_bytes=_int(
                "COLLECTOR_DAILY_EVENT_BUDGET_BYTES",
                _env("COLLECTOR_DAILY_EVENT_BUDGET_BYTES", defaults["daily_budget"]),
                minimum=0,
            ),
            book_publish_interval_seconds=_float(
                "COLLECTOR_BOOK_INTERVAL_SECONDS", _env("COLLECTOR_BOOK_INTERVAL_SECONDS", defaults["book_interval"])
            ),
            book_tail_interval_seconds=_float(
                "COLLECTOR_BOOK_TAIL_INTERVAL_SECONDS", _env("COLLECTOR_BOOK_TAIL_INTERVAL_SECONDS", defaults["book_tail_interval"])
            ),
            book_tail_seconds=_int(
                "COLLECTOR_BOOK_TAIL_SECONDS", _env("COLLECTOR_BOOK_TAIL_SECONDS", defaults["book_tail_seconds"]), minimum=0
            ),
            min_free_bytes=_int(
                "COLLECTOR_MIN_FREE_BYTES", _env("COLLECTOR_MIN_FREE_BYTES", defaults["min_free_bytes"]), minimum=0
            ),
            max_disk_usage_bytes=_int(
                "COLLECTOR_MAX_DISK_USAGE_BYTES",
                _env("COLLECTOR_MAX_DISK_USAGE_BYTES", defaults["max_disk_usage_bytes"]),
                minimum=0,
            ),
            deduplicate_metadata=profile == "free",
        )

    def snapshot(self) -> dict:
        return {
            "profile": self.profile,
            "discovery_interval_seconds": self.discovery_interval_seconds,
            "unsettled_limit": self.unsettled_limit,
            "market_offsets": list(self.market_offsets),
            "raw_sample_seconds": self.raw_sample_seconds,
            "reference_interval_seconds": self.reference_interval_seconds,
            "enable_reference_trades": self.enable_reference_trades,
            "daily_event_budget_bytes": self.daily_event_budget_bytes,
            "book_publish_interval_seconds": self.book_publish_interval_seconds,
            "book_tail_interval_seconds": self.book_tail_interval_seconds,
            "book_tail_seconds": self.book_tail_seconds,
            "min_free_bytes": self.min_free_bytes,
            "max_disk_usage_bytes": self.max_disk_usage_bytes,
            "deduplicate_metadata": self.deduplicate_metadata,
        }
