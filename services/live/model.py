"""Pure checks shared by the optional Polymarket executor and its isolated tests."""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from fractions import Fraction
from pathlib import Path

from pqcollector.markets import parse_market
from services.engine.model import Book, dec, fee, lot


class GuardError(ValueError):
    """A fixed, non-sensitive reason for withholding an order."""


def require(condition, reason):
    if not condition:
        raise GuardError(reason)


@dataclass(frozen=True, repr=False)
class Credentials:
    wallet_address: str
    owner_address: str
    owner_api_key: str
    owner_api_secret: str
    owner_api_passphrase: str
    session_address: str
    session_private_key: str
    session_api_key: str
    session_api_secret: str
    session_api_passphrase: str

    @classmethod
    def load(cls, path):
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
        require(isinstance(raw, dict) and set(raw) == set(cls.__annotations__), "CREDENTIAL_SCHEMA")
        require(
            all(isinstance(value, str) and value.strip() == value and value for value in raw.values()),
            "CREDENTIAL_SCHEMA",
        )
        addresses = [raw[key].lower() for key in ("wallet_address", "owner_address", "session_address")]
        require(
            all(re.fullmatch(r"0x[0-9a-f]{40}", value) and int(value, 16) for value in addresses)
            and len(set(addresses)) == 3,
            "CREDENTIAL_ADDRESS",
        )
        require(re.fullmatch(r"(?:0x)?[0-9a-fA-F]{64}", raw["session_private_key"]), "CREDENTIAL_SCHEMA")
        return cls(**raw)


def session_expiry(payload, credentials, now):
    require(payload["wallet"].lower() == credentials.wallet_address.lower(), "SESSION_WALLET")
    matches = [s for s in payload["signers"] if s["address"].lower() == credentials.session_address.lower()]
    require(len(matches) == 1 and matches[0]["scopes"] == ["CLOB"], "SESSION_SCOPE")
    signer = matches[0]
    # The pinned SDK normalizes its wire keys to snake_case. Accept the equivalent
    # official JSON key, and reject contradictory representations.
    values = [signer[k] for k in ("validUntil", "valid_until") if k in signer]
    require(len(values) == 1 and type(values[0]) is int, "SESSION_EXPIRY")
    expiry = datetime.fromtimestamp(values[0], UTC)
    require(expiry > now, "SESSION_EXPIRED")
    return expiry


@dataclass(frozen=True)
class Leg:
    token: str
    price: Decimal
    principal: Decimal


@dataclass(frozen=True)
class Pair:
    quantity: Decimal
    legs: tuple[Leg, Leg]
    expires_at: datetime
    oldest_book: datetime
    estimated_funds: Decimal


def verify_market(row, raw, now):
    require(raw.get("version") == "v1", "MARKET_PROTOCOL")
    require(raw.get("tradingEligible") is True and raw.get("closed") is False, "MARKET_ELIGIBILITY")
    market = parse_market(raw, now)
    require(
        all(
            market[k] == row[k]
            for k in ("market_id", "slug", "condition_id", "asset", "timeframe", "up_token_id", "down_token_id")
        ),
        "MARKET_IDENTITY",
    )
    require(
        row["rule_verified"]
        and not row["rule_changed"]
        and row["accepting_orders"]
        and row["resolved_outcome"] is None
        and market["rule_verified"]
        and market["accepting_orders"]
        and market["market_status"] == "ACTIVE"
        and market["fee_rate"] is not None
        and market["rule_version"] == row["settlement_rule_version"]
        and market["fee_schedule_version"] == row["fee_schedule_version"],
        "MARKET_RULE",
    )
    return market


def plan_pair(row, market, books, now, per_buy, remaining):
    cfg, evidence = row["config"], row["evidence"]
    selected = row["strategy_id"], row["strategy_version"]
    if selected != (row["account_strategy"], row["account_version"]):
        members = [m for m in cfg.get("strategies", []) if (m["strategy_id"], m["strategy_version"]) == selected]
        require(len(members) == 1, "STRATEGY_VERSION")
        # Paper portfolios override only these parameters in engine.execution.process.
        cfg = {
            **cfg,
            **{k: v for k, v in members[0]["parameters"].items() if k in ("minimum_edge", "tail_seconds")},
        }
    require(
        row["strategy_id"] == "S01"
        and row["action"] == "PAIR"
        and row["reason"] == "POSITIVE_THEORETICAL_EDGE"
        and 0 <= (now - row["created_at"]).total_seconds() <= 3,
        "SIGNAL",
    )
    require(len(books) == len(evidence["books"]) == len(evidence["limits"]) == 2, "PAIR_MAPPING")
    current = {b.asset_id: b for b in books}
    sides = {}
    for record, raw_limit in zip(evidence["books"], evidence["limits"], strict=True):
        data = record["book"]
        side = data["side"]
        require(side in ("UP", "DOWN") and side not in sides, "PAIR_MAPPING")
        token = market[side.lower() + "_token_id"]
        require(
            data["token_id"] == token
            and data["rule_version"] == market["rule_version"]
            and data["asset"] == market["asset"]
            and data["timeframe"] == market["timeframe"],
            "PAIR_MAPPING",
        )
        book = current[token]
        require(book.condition_id == market["condition_id"] and book.neg_risk is False, "BOOK_MARKET")
        # Reuse the paper engine's timestamp, tick, size, spread and depth validation.
        live_data = dict(
            data,
            tick_size=str(book.tick_size),
            minimum_order_size=str(book.min_order_size),
            fee_rate=market["fee_rate"],
            fee_exponent=market["fee_exponent"],
            start_time=market["start_time"],
            end_time=market["end_time"],
            asks=[[str(x.price), str(x.size)] for x in book.asks],
            bids=[[str(x.price), str(x.size)] for x in book.bids],
        )
        parsed = Book.parse(
            {
                "data": live_data,
                "quality_flags": [],
                "event_time": book.timestamp,
                "received_at": now,
                "event_id": record["event_id"],
            },
            now,
        )
        limit = dec(raw_limit)
        require(
            0 < limit < 1
            and limit % parsed.tick == 0
            and parsed.asks[0][0] + int(cfg["impact_ticks"]) * parsed.tick <= limit,
            "LIMIT_PRICE",
        )
        sides[side] = (token, limit, parsed, book.timestamp)
    up, down = (sides[side] for side in ("UP", "DOWN"))
    require(abs((up[3] - down[3]).total_seconds()) <= 1, "BOOK_SKEW")
    total_price = up[1] + down[1]
    quantity = min(
        dec(evidence["quantity"]),
        remaining / total_price,
        *(per_buy / leg[1] for leg in (up, down)),
        *(lot(leg[2].asks[0][1] * dec(cfg["depth_fraction"])) for leg in (up, down)),
    )
    # Each FOK BUY signs an amount in whole cents. Choose a common number of
    # hundredths of a share that yields exact cents on both price grids.
    step = Decimal(math.lcm(Fraction(up[1]).denominator, Fraction(down[1]).denominator)) / 100
    quantity = (quantity // step) * step
    require(quantity >= max(up[2].minimum, down[2].minimum) and quantity > 0, "BELOW_MINIMUM")
    legs = tuple(Leg(leg[0], leg[1], quantity * leg[1]) for leg in (up, down))
    rates = [leg[2].rate * dec(cfg.get("cost_multiplier", "1")) for leg in (up, down)]
    cost = sum(leg.principal + fee(quantity, leg.price, rate) for leg, rate in zip(legs, rates, strict=True))
    require(quantity - cost > quantity * dec(cfg.get("minimum_edge", "0")), "NO_NET_EDGE")
    funds = sum(
        (
            leg.principal + fee(quantity, leg.price, book[2].rate)
            for leg, book in zip(legs, (up, down), strict=True)
        ),
        Decimal(0),
    )
    return Pair(quantity, (legs[0], legs[1]), min(up[2].close, down[2].close), min(up[3], down[3]), funds)


def prepare_signed(client, pair, wallet):
    signed = []
    for leg in pair.legs:
        order = client.create_market_order(
            asset_id=leg.token, side="BUY", amount=str(leg.principal), max_price=str(leg.price), order_type="FOK"
        )
        # Deposit Wallet EIP-1271 puts the wallet in both wire fields. The SDK
        # wraps the delegated session address in the signature.
        require(
            order.maker.lower() == order.signer.lower() == wallet.lower()
            and order.signature_type == 3
            and order.order_type == "FOK"
            and not order.post_only
            and order.side == "BUY"
            and order.token_id == leg.token
            and order.maker_amount == leg.principal * 1_000_000
            and order.taker_amount == pair.quantity * 1_000_000,
            "SIGNED_LIMIT",
        )
        signed.append(order)
    return signed
