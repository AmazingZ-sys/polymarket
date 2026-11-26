from __future__ import annotations

import hashlib
import json
import re
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

# SHA-256 of the exact official description captured for each asset.
# A changed definition requires a new independent rules review.
DESCRIPTION_DIGESTS = {
    "BTC": "485ceb1dabc4aa12fb42c76184563b7378f01e5de9ded73df049c0d191cd5ad1",
    "ETH": "189561e0d8b49e0e5e14327991c6f6b124f1523ce8f58627d431aae9d155b39f",
}


def now() -> datetime:
    return datetime.now(UTC)


def stamp(value: str) -> datetime:
    result = datetime.fromisoformat(value)
    if result.tzinfo is None:
        raise ValueError("missing timestamp timezone")
    return result.astimezone(UTC)


def digest(value: Any) -> str:
    wire = json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), default=str)
    return hashlib.sha256(wire.encode()).hexdigest()


def number(value: Any) -> Decimal:
    try:
        result = Decimal(str(value))
    except InvalidOperation as exc:
        raise ValueError("invalid decimal") from exc
    if not result.is_finite():
        raise ValueError("nonfinite decimal")
    return result


def array(value: Any) -> list:
    result = json.loads(value) if isinstance(value, str) else value
    if not isinstance(result, list):
        raise TypeError("expected array")
    return result


def candidates(at: datetime) -> list[str]:
    timestamp = int(at.timestamp())
    return [
        f"{asset}-updown-{minutes}m-{timestamp // step * step + offset * step}"
        for offset in (0, 1, -1, -2)
        for asset in ("btc", "eth")
        for minutes in (5, 15)
        for step in [60 * minutes]
    ]


def parse_market(raw: dict, at: datetime) -> dict:
    slug = raw["slug"]
    match = re.fullmatch(r"(btc|eth)-updown-(5|15)m-(\d+)", slug)
    if match is None:
        raise ValueError("out of scope market")
    symbol, duration, epoch = match.groups()
    asset, minutes = symbol.upper(), int(duration)
    config = raw.get("cryptoMarketConfig") or {}
    start = stamp(raw["eventStartTime"])
    end = stamp(raw["endDate"])
    names = [s.upper() for s in array(raw["outcomes"])]
    tokens = array(raw["clobTokenIds"])
    if sorted(names) != ["DOWN", "UP"] or len(tokens) != 2 or len(set(tokens)) != 2:
        raise ValueError("ambiguous outcome tokens")
    if not all(isinstance(token, str) and token.isdigit() for token in tokens):
        raise ValueError("invalid token identifier")
    token_by_side = dict(zip(names, tokens, strict=True))
    source = raw.get("resolutionSource", "")
    description = raw.get("description", "")
    rule_fields = {
        k: raw.get(k)
        for k in (
            "resolutionSource",
            "description",
            "cryptoMarketConfig",
            "resolutionMechanism",
            "negRisk",
            "fallbackResolutionType",
            "eventStartTime",
            "endDate",
        )
    }
    rule_fields["token_by_side"] = token_by_side
    schedule = raw.get("feeSchedule") or {}
    valid_rule = (
        config.get("asset") == symbol
        and config.get("duration") == f"{minutes}m"
        and config.get("twapEnabled") is True
        and config.get("twapLookbackSeconds") == 60
        and source == f"https://data.chain.link/streams/{symbol}-usd-twap-60s-streams"
        and hashlib.sha256(description.encode()).hexdigest() == DESCRIPTION_DIGESTS[asset]
        and raw.get("resolutionMechanism") == "oracle"
        and raw.get("negRisk") is False
        and raw.get("fallbackResolutionType") == "none"
        and re.fullmatch("0x[0-9a-fA-F]{64}", raw["conditionId"]) is not None
        and start.timestamp() == int(epoch)
        and (end - start).total_seconds() == minutes * 60
    )
    fee_rate = schedule.get("rate")
    fee_ok = (
        raw.get("feesEnabled") is True
        and schedule.get("exponent") == 1
        and schedule.get("takerOnly") is True
        and fee_rate is not None
        and 0 <= number(fee_rate) <= 1
    )
    meta: dict = (
        next((e.get("eventMetadata", {}) for e in raw.get("events", []) if e.get("slug") == slug), {}) or {}
    )
    reference = meta.get("priceToBeat")
    final = meta.get("finalPrice")
    result = None
    closed = raw.get("closed") is True
    accepting = raw.get("acceptingOrders") is True and not closed
    resolution_state = raw.get("umaResolutionStatus", "").lower()
    if (
        valid_rule
        and closed
        and not accepting
        and raw.get("acceptingOrders") is False
        and resolution_state == "resolved"
    ):
        prices = [number(x) for x in array(raw.get("outcomePrices", []))]
        if sorted(prices) == [0, 1] and reference is not None and final is not None:
            reported = names[prices.index(Decimal(1))]
            from_prices = "UP" if number(final) >= number(reference) else "DOWN"
            if from_prices == reported and min(number(reference), number(final)) > 0:
                result = reported
    if result:
        status = "RESOLVED"
    elif raw.get("archived") or resolution_state in {"disputed", "resolved"}:
        status = "ANOMALY"
    elif closed or end <= at:
        status = "AWAITING_RESOLUTION"
    elif start > at:
        status = "UPCOMING"
    else:
        status = "ACTIVE"
    return {
        "market_id": str(raw["id"]),
        "condition_id": raw["conditionId"],
        "asset": asset,
        "timeframe": minutes,
        "slug": slug,
        "title": raw["question"],
        "start_time": start.isoformat(),
        "end_time": end.isoformat(),
        "up_token_id": token_by_side["UP"],
        "down_token_id": token_by_side["DOWN"],
        "resolution_source": source,
        "settlement_rule": description,
        "rule_fields": rule_fields,
        "rule_version": digest(rule_fields),
        "rule_verified": valid_rule,
        "twap_window": config.get("twapLookbackSeconds"),
        "fee_schedule_version": digest(schedule),
        "fee_schedule": schedule,
        "fee_rate": str(fee_rate) if fee_ok else None,
        "fee_exponent": schedule.get("exponent"),
        "tick_size": raw.get("orderPriceMinTickSize"),
        "minimum_order_size": raw.get("orderMinSize"),
        "accepting_orders": accepting and bool(raw.get("enableOrderBook")) and raw.get("active") is True,
        "reference_price": str(reference) if reference is not None else None,
        "market_status": status,
        "outcome": result,
        "raw": raw,
    }
