"""Read-only reconciliation of signer-owned FOK orders and Polygon V2 exchange receipts."""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal

import httpx

from .venue import EXCHANGE, UNIT

# keccak256 of CTF Exchange V2 ITradingEE.OrderFilled, with Side encoded as uint8.
ORDER_FILLED = "0xd543adfd945773f1a62f74f0ee55a5e3b9b1a28262980ba90b1a89f2ea84d8ee"
HASH = re.compile(r"0x[0-9a-fA-F]{64}")
RPC = "https://polygon.drpc.org"


@dataclass(frozen=True)
class LegResult:
    status: str
    quantity: Decimal | None = None
    principal: Decimal | None = None
    fee: Decimal | None = None


REJECTED = LegResult("REJECTED", Decimal(0), Decimal(0), Decimal(0))
UNKNOWN = LegResult("UNKNOWN")
PENDING = LegResult("PENDING")


def receipt(tx_hash):
    with httpx.Client(trust_env=False, follow_redirects=False, timeout=4.0) as http:

        def rpc(method, params):
            r = http.post(RPC, json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params})
            r.raise_for_status()
            payload = r.json()
            if payload.get("id") != 1 or "error" in payload:
                raise ValueError("RPC response mismatch")
            return payload["result"]

        if rpc("eth_chainId", []) != "0x89":
            return None
        r = rpc("eth_getTransactionReceipt", [tx_hash])
        if not r or r["status"] != "0x1" or r["transactionHash"].lower() != tx_hash:
            return None
        finalized = rpc("eth_getBlockByNumber", ["finalized", False])
        if int(finalized["number"], 16) < int(r["blockNumber"], 16):
            return None
        block = rpc("eth_getBlockByNumber", [r["blockNumber"], False])
        if block["hash"].lower() != r["blockHash"].lower():
            return None
        return r


def receipt_amounts(tx_hashes, order, wallet, token, expected_size):
    quantity = principal = fee = Decimal(0)
    for tx in tx_hashes:
        r = receipt(tx)
        if r is None:
            return None
        indices = set()
        for log in r["logs"]:
            topics = [v.lower() for v in log["topics"]]
            if log["address"].lower() != EXCHANGE or topics[:2] != [ORDER_FILLED, order.lower()]:
                continue
            if (
                len(topics) != 4
                or topics[2] != "0x" + wallet.lower()[2:].zfill(64)
                or topics[3] != "0x" + EXCHANGE[2:].zfill(64)
                or log.get("removed")
                or log["transactionHash"].lower() != tx
                or log["blockHash"].lower() != r["blockHash"].lower()
                or log["logIndex"] in indices
                or not re.fullmatch(r"0x[0-9a-fA-F]{448}", log["data"])
            ):
                return None
            indices.add(log["logIndex"])
            data = log["data"][2:]
            side, asset, paid, received, cost = (int(data[i : i + 64], 16) for i in range(0, 320, 64))
            if side != 0 or asset != int(token) or received == 0 or paid == 0:
                return None
            quantity += received / UNIT
            principal += paid / UNIT
            fee += cost / UNIT
        if not indices:
            return None
    if quantity == expected_size:
        return principal, fee
    return None


def leg_result(client, wallet, row, side, oid, rejected, placement):
    if not oid:
        return REJECTED if rejected else UNKNOWN
    if rejected or not HASH.fullmatch(oid):
        return UNKNOWN
    from polymarket.errors import PolymarketError
    from polymarket.models.clob.account import ClobTrade

    try:
        order = client.get_order(order_id=oid)
        expected_token = row[side + "_token_id"]
        if (
            order.id.lower() != oid.lower()
            or order.condition_id.lower() != row["condition_id"].lower()
            or order.asset_id != expected_token
            or order.side != "BUY"
            or order.order_type.upper() != "FOK"
            or order.maker_address.lower() != wallet.lower()
        ):
            return UNKNOWN
        status = order.status.upper().removeprefix("ORDER_STATUS_")
        if status in {"CANCELED", "CANCELLED", "UNMATCHED"} and order.size_matched == 0:
            return REJECTED
        if status not in {"MATCHED", "FILLED"} or order.size_matched <= 0:
            return PENDING if status in {"MATCHED", "LIVE", "DELAYED"} else UNKNOWN
        listing = client.list_account_trades(asset_id=expected_token, market=order.condition_id)
        matched: dict[str, ClobTrade] = {}
        for trade in listing.iter_items():
            if trade.taker_order_id.lower() != oid.lower():
                continue
            if (
                trade.condition_id.lower() != order.condition_id.lower()
                or trade.asset_id != expected_token
                or trade.side != "BUY"
                or trade.trader_side != "TAKER"
                or trade.size <= 0
            ):
                return UNKNOWN
            if trade.id in matched and matched[trade.id] != trade:
                return UNKNOWN
            matched[trade.id] = trade
        expected = set(order.associate_trades)
        if placement is not None and getattr(placement, "order_id", "").lower() == oid.lower():
            expected.update(placement.trade_ids)
        if not expected.issubset(matched) or not matched:
            return PENDING
        if not all(t.status == "CONFIRMED" for t in matched.values()):
            return UNKNOWN if any(t.status == "FAILED" for t in matched.values()) else PENDING
        quantity = sum((t.size for t in matched.values()), Decimal(0))
        if quantity != order.size_matched:
            return UNKNOWN
        # Actual maker fills can have different prices. A CLOB display price or
        # the signed maximum cannot establish the exact collateral debited.
        hashes = {
            t.transaction_hash.lower()
            for t in matched.values()
            if t.transaction_hash and HASH.fullmatch(t.transaction_hash)
        }
        money = None
        if all(t.transaction_hash and HASH.fullmatch(t.transaction_hash) for t in matched.values()):
            try:
                money = receipt_amounts(hashes, oid, wallet, expected_token, quantity)
            except (httpx.HTTPError, ValueError, KeyError, TypeError, AttributeError):
                pass
        return LegResult("FILLED", quantity, *(money or (None, None)))
    except (PolymarketError, httpx.HTTPError, ValueError, KeyError, TypeError, AttributeError):
        return UNKNOWN


def classify(client, wallet, row, order_ids, *, rejected=(False, False), responses=None):
    """Read venue evidence for already recorded orders. Only verified debits get amounts."""
    placements = responses if responses is not None else (None, None)
    up, down = (
        leg_result(client, wallet, row, side, oid, flag, placement)
        for side, oid, flag, placement in zip(("up", "down"), order_ids, rejected, placements, strict=True)
    )
    if order_ids[0] and order_ids[0] == order_ids[1]:
        up = down = UNKNOWN
    states = {up.status, down.status}
    if states == {"FILLED"}:
        state = "CONFIRMED" if up.quantity == down.quantity else "EXPOSED"
    elif states == {"REJECTED"}:
        state = "REJECTED"
    elif (
        "REJECTED" in states
        and ("FILLED" in states or any(order_ids))
        or "FILLED" in states
        and "UNKNOWN" in states
    ):
        state = "EXPOSED"
    elif "UNKNOWN" in states:
        state = "UNKNOWN"
    else:
        state = "RESERVED"
    return (
        state,
        *order_ids,
        up.principal,
        down.principal,
        up.fee,
        down.fee,
        "pUSD" if up.fee is not None else None,
        "pUSD" if down.fee is not None else None,
    )
