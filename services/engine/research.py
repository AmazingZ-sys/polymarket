"""Versioned observation strategies. Inputs must already be validated as-of the decision."""

from decimal import DecimalException

from .model import Book, dec, iso


def observe(sid: str, book: Book, cfg: dict, peer: dict | None = None) -> tuple[str, dict]:
    p = (book.bids[0][0] + book.asks[0][0]) / 2
    remaining = (book.close - book.received).total_seconds()
    evidence: dict = {
        "event_id": book.event_id,
        "book": book.data,
        "remaining_seconds": remaining,
        "market_probability": str(p if book.side == "UP" else 1 - p),
    }
    if sid == "S02":
        evidence["window_seconds"] = int(cfg["tail_seconds"])
        evidence["reference_price"] = book.data.get("reference_price")
        evidence["oracle_coverage"] = "UNVERIFIED"
        try:
            d = book.data
            observed = iso(d["oracle_event_time"])
            received = iso(d["oracle_received_at"])
            compatible = d["oracle_source"] == "chainlink" and int(d["oracle_window"]) == int(d["twap_window"])
            if (
                compatible
                and observed <= received <= book.received
                and 0 <= (book.received - observed).total_seconds() <= 3
            ):
                reference, current = dec(d["reference_price"]), dec(d["oracle_price"])
                if reference > 0 and current > 0 and d["oracle_event_id"]:
                    evidence.update(
                        oracle_coverage="CAUSAL_OBSERVATION",
                        oracle_event_id=d["oracle_event_id"],
                        observed_margin=str(current - reference),
                        relative_margin=str(current / reference - 1),
                    )
        except (KeyError, TypeError, ValueError, DecimalException):
            pass
        return ("TAIL_OBSERVATION" if remaining <= evidence["window_seconds"] else "OUTSIDE_TAIL"), evidence
    if sid == "S03":
        return "MARKET_PROBABILITY", evidence
    if sid == "S04":
        if peer:
            evidence["peer_event_id"] = peer["event_id"]
            evidence["peer_market_id"] = peer["market_id"]
            evidence["probability_spread"] = str(dec(evidence["market_probability"]) - dec(peer["p"]))
        return ("CROSS_PERIOD_OBSERVATION" if peer else "AWAITING_OVERLAPPING_HORIZON"), evidence
    return "NO_TRADE_BASELINE", evidence
