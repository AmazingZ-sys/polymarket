from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal

from .markets import number


def frame_time(frame: dict) -> datetime:
    return datetime.fromtimestamp(int(frame["timestamp"]) / 1000, UTC)


@dataclass
class Book:
    bids: dict[Decimal, Decimal] = field(default_factory=dict)
    asks: dict[Decimal, Decimal] = field(default_factory=dict)
    timestamp: datetime | None = None

    @staticmethod
    def level(side: dict, p, quantity):
        price, size = number(p), number(quantity)
        if not 0 <= price <= 1 or size < 0:
            raise ValueError("INVALID_LEVEL")
        if size:
            side[price] = size
        else:
            side.pop(price, None)

    def apply(self, frame: dict, changes: list[dict] | None = None):
        event_time = frame_time(frame)
        if self.timestamp and self.timestamp > event_time:
            raise ValueError("OUT_OF_ORDER")
        if changes is None:
            self.bids.clear()
            self.asks.clear()
            for key, side in (("bids", self.bids), ("asks", self.asks)):
                for level in frame[key]:
                    self.level(side, level["price"], level["size"])
        else:
            if not self.timestamp:
                raise ValueError("MISSING_SNAPSHOT")
            for change in changes:
                side_name = change["side"]
                if side_name not in ("BUY", "SELL"):
                    raise ValueError("UNKNOWN_BOOK_SIDE")
                self.level(self.bids if side_name == "BUY" else self.asks, change["price"], change["size"])
            last = changes[-1]
            for key, expected in (
                ("best_bid", max(self.bids, default=0)),
                ("best_ask", min(self.asks, default=1)),
            ):
                if key in last and number(last[key]) != expected:
                    raise ValueError("TOP_MISMATCH")
        if self.bids and self.asks and max(self.bids) >= min(self.asks):
            raise ValueError("CROSSED_BOOK")
        self.timestamp = event_time

    def snapshot(self) -> dict:
        return {
            "asks": [[str(k), str(self.asks[k])] for k in sorted(self.asks)],
            "bids": [[str(k), str(self.bids[k])] for k in sorted(self.bids, reverse=True)],
        }


def quality(event_time: datetime, received: datetime, market: dict) -> list[str]:
    flags = []
    lag = (received - event_time).total_seconds()
    if lag < -1:
        flags.append("CLOCK_AHEAD")
    if lag > 3:
        flags.append("STALE_EVENT")
    if (received - market["observed_at"]).total_seconds() > 120:
        flags.append("STALE_METADATA")
    if not market["rule_verified"]:
        flags.append("RULE_UNVERIFIED")
    if market["fee_rate"] is None:
        flags.append("FEE_UNVERIFIED")
    if not market["tick_size"] or not market["minimum_order_size"]:
        flags.append("MISSING_PRECISION")
    return flags
