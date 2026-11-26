"""Causal book validation and conservative IOC execution primitives."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal
from typing import Any

ZERO = Decimal(0)
ONE = Decimal(1)
MONEY = Decimal("0.00000001")
LOT = Decimal("0.01")
MAX_AGE = 3.0


def dec(raw: Any) -> Decimal:
    value = Decimal(str(raw))
    if not value.is_finite():
        raise ValueError("Nonfinite decimal")
    return value


def iso(raw: str) -> datetime:
    instant = datetime.fromisoformat(raw)
    if instant.tzinfo is None:
        raise ValueError("Timestamp requires timezone")
    return instant


def money(v: Decimal) -> Decimal:
    return v.quantize(MONEY, rounding=ROUND_CEILING)


def lot(v: Decimal) -> Decimal:
    return v.quantize(LOT, rounding=ROUND_FLOOR)


@dataclass(frozen=True)
class Book:
    side: str
    asks: list[tuple[Decimal, Decimal]]
    bids: list[tuple[Decimal, Decimal]]
    tick: Decimal
    minimum: Decimal
    rate: Decimal
    rule: str
    close: datetime
    event_id: str
    received: datetime
    data: dict

    @classmethod
    def parse(cls, e: dict, at: datetime) -> Book:
        data = e["data"]
        if e["quality_flags"] or data["rule_verified"] is not True or data["accepting_orders"] is not True:
            raise ValueError("Unverified book")
        if (
            not 0 <= (at - e["event_time"]).total_seconds() <= MAX_AGE
            or not e["event_time"] <= e["received_at"] <= at
        ):
            raise ValueError("Stale, future or unobserved book")
        close = iso(data["end_time"])
        if data.get("order_acceptance_end_time"):
            close = min(close, iso(data["order_acceptance_end_time"]))
        if not iso(data["start_time"]) <= at < close:
            raise ValueError("Outside acceptance window")
        tick, minimum, rate = map(dec, (data["tick_size"], data["minimum_order_size"], data["fee_rate"]))
        if not 0 < tick < 1 or minimum <= 0 or not 0 <= rate <= 1 or dec(data["fee_exponent"]) != 1:
            raise ValueError("Unverified tick, size or fee")
        if data["side"] not in {"UP", "DOWN"} or not data["rule_version"] or not data["token_id"]:
            raise ValueError("Invalid binary mapping")

        def levels(key: str, descending: bool) -> list[tuple[Decimal, Decimal]]:
            result = [(dec(p), dec(q)) for p, q in data[key]]
            if not result or any(not 0 < p < 1 or q <= 0 or p % tick for p, q in result):
                raise ValueError("Invalid book level")
            if len({p for p, _ in result}) != len(result):
                raise ValueError("Duplicate book level")
            return sorted(result, reverse=descending)

        asks, bids = levels("asks", False), levels("bids", True)
        if bids[0][0] >= asks[0][0]:
            raise ValueError("Crossed book")
        return cls(
            data["side"],
            asks,
            bids,
            tick,
            minimum,
            rate,
            data["rule_version"],
            close,
            e["event_id"],
            e["received_at"],
            data,
        )


def fee(q: Decimal, p: Decimal, rate: Decimal) -> Decimal:
    return money(q * rate * p * (ONE - p))


@dataclass(frozen=True)
class Match:
    quantity: Decimal
    notional: Decimal
    fees: Decimal
    slippage: Decimal
    levels: list[list[str]]


def match(book: Book, cfg: dict, quantity: Decimal, limit: Decimal) -> Match:
    total = notional = fees = slippage = ZERO
    levels = []
    for ask, size in book.asks:
        price = ask + book.tick * int(cfg["impact_ticks"])
        if price > limit or price >= 1:
            break
        take = min(quantity - total, lot(size * dec(cfg["depth_fraction"])))
        if take > 0:
            total += take
            notional += price * take
            fees += fee(take, price, book.rate)
            slippage += (price - book.asks[0][0]) * take
            levels.append([str(price), str(take)])
        if total == quantity:
            break
    return Match(total, money(notional), money(fees), money(slippage), levels)


def reserve(q: Decimal, limit: Decimal, rate: Decimal) -> Decimal:
    # Upper bound over all eligible levels, including per-level fee rounding.
    # At most one level per price tick, with at most one fill per 0.01 share.
    return money(q * (limit + rate / 4) + q / LOT * MONEY)


def opportunity(pair: list[Book], cfg: dict, budget: Decimal) -> tuple[Decimal, list[Decimal], str]:
    if len(pair) != 2 or {b.side for b in pair} != {"UP", "DOWN"}:
        return ZERO, [], "INCOMPLETE_PAIR"
    if (
        pair[0].rule != pair[1].rule
        or pair[0].data["token_id"] == pair[1].data["token_id"]
        or abs((pair[0].received - pair[1].received).total_seconds()) > 1
    ):
        return ZERO, [], "PAIR_SKEW_OR_RULE"
    limits = [b.asks[0][0] + int(cfg["impact_ticks"]) * b.tick for b in pair]
    if any(p >= 1 for p in limits):
        return ZERO, limits, "LIMIT_OUTSIDE_MARKET"
    unit_cap = sum(reserve(ONE, p, b.rate) for b, p in zip(pair, limits))
    q = min(lot(budget / unit_cap), *(lot(b.asks[0][1] * dec(cfg["depth_fraction"])) for b in pair))
    if q < max(b.minimum for b in pair):
        return ZERO, limits, "BELOW_MINIMUM_OR_RISK_CAP"
    full_cost = sum(money(q * p) + fee(q, p, b.rate) for b, p in zip(pair, limits))
    if q - full_cost <= q * dec(cfg.get("minimum_edge", "0")):
        return ZERO, limits, "NO_NET_EDGE"
    return q, limits, "POSITIVE_THEORETICAL_EDGE"
