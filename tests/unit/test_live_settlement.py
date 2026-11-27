"""Synthetic SDK response and Polygon receipt fixtures. No wallet or live network."""

from decimal import Decimal
from types import SimpleNamespace

import pytest

from services.live import settlement as s

account = pytest.importorskip("polymarket.models.clob.account")
from polymarket.models.clob.order_response import AcceptedOrder

WALLET = "0x" + "ab" * 20
CONDITION = "0x" + "55" * 32
UP, DOWN = ("0x" + c * 64 for c in ("a", "b"))
TX = "0x" + "12" * 32
BLOCK = "0x" + "34" * 32
ROW = {"condition_id": CONDITION, "up_token_id": "11", "down_token_id": "12"}
D = Decimal


def order(oid, token):
    return account.OpenOrder.model_validate(
        {
            "id": oid,
            "market": CONDITION,
            "asset_id": token,
            "owner": "TEST",
            "maker_address": WALLET,
            "side": "BUY",
            "price": "0.75",
            "original_size": "3",
            "size_matched": "3",
            "outcome": "UP" if token == "11" else "DOWN",
            "order_type": "FOK",
            "status": "ORDER_STATUS_MATCHED",
            "associate_trades": [token],
            "created_at": 1700000000,
        }
    )


def trade(oid, token):
    return account.ClobTrade.model_validate(
        {
            "id": token,
            "market": CONDITION,
            "asset_id": token,
            "owner": "TEST",
            "maker_address": WALLET,
            "side": "BUY",
            "trader_side": "TAKER",
            "size": "3",
            "price": "0.75",
            "outcome": "UP" if token == "11" else "DOWN",
            "status": "TRADE_STATUS_CONFIRMED",
            "taker_order_id": oid,
            "transaction_hash": TX,
            "fee_rate_bps": "1",
            "bucket_index": 0,
            "maker_orders": [],
            "match_time": 1700000000,
            "last_update": 1700000001,
        }
    )


class Client:
    def __init__(self):
        self.orders = {oid: order(oid, token) for oid, token in ((UP, "11"), (DOWN, "12"))}
        self.trades = {token: [trade(oid, token)] for oid, token in ((UP, "11"), (DOWN, "12"))}

    def get_order(self, *, order_id):
        return self.orders[order_id]

    def list_account_trades(self, *, asset_id, market):
        assert market == CONDITION
        return SimpleNamespace(iter_items=lambda: iter(self.trades[asset_id]))


def log(oid, token, price_units, fee_units, log_index):
    words = (0, token, price_units, 3000000, fee_units, 0, 0)
    return {
        "address": s.EXCHANGE,
        "topics": [s.ORDER_FILLED, oid, "0x" + WALLET[2:].zfill(64), "0x" + s.EXCHANGE[2:].zfill(64)],
        "data": "0x" + "".join(f"{n:064x}" for n in words),
        "logIndex": hex(log_index),
        "transactionHash": TX,
        "blockHash": BLOCK,
        "removed": False,
    }


def sample_receipt():
    return {"logs": [log(UP, 11, 1220010, 12340, 1), log(DOWN, 12, 1720030, 11110, 2)], "blockHash": BLOCK}


def test_read_only_reconciliation_recovers_exact_collateral_and_fees_from_matching_finalized_logs(monkeypatch):
    monkeypatch.setattr(s, "receipt", lambda tx: sample_receipt() if tx == TX else None)
    client = Client()
    placements = [
        AcceptedOrder(
            order_id=oid,
            status="matched",
            making_amount=D("2.25"),
            taking_amount=D(3),
            trade_ids=(token,),
            transactions_hashes=(TX,),
        )
        for oid, token in ((UP, "11"), (DOWN, "12"))
    ]
    expected = ("CONFIRMED", UP, DOWN, D("1.220010"), D("1.720030"), D("0.012340"), D("0.011110"), "pUSD", "pUSD")
    assert s.classify(client, WALLET, ROW, (UP, DOWN), responses=placements) == expected
    # A restarted poll only needs the durable venue order IDs and market identity.
    assert s.classify(client, WALLET, ROW, (UP, DOWN)) == expected


@pytest.mark.parametrize(
    "invalid", ["other-exchange", "bad-quantity", "removed", "repeat-log", "bad-hash", "missing"]
)
def test_quote_and_fee_rate_do_not_substitute_for_missing_settlement_evidence(monkeypatch, invalid):
    r = sample_receipt()
    entry = r["logs"][0]
    if invalid == "other-exchange":
        entry["address"] = WALLET
    if invalid == "bad-quantity":
        entry["data"] = entry["data"].replace(f"{3000000:064x}", f"{2999999:064x}")
    if invalid == "removed":
        entry["removed"] = True
    if invalid == "repeat-log":
        r["logs"].append(entry)
    if invalid == "bad-hash":
        entry["transactionHash"] = BLOCK
    monkeypatch.setattr(s, "receipt", lambda _tx: None if invalid == "missing" else r)
    result = s.classify(Client(), WALLET, ROW, (UP, DOWN))
    assert result[0] == "CONFIRMED"
    assert result[3] is None and result[5] is None and result[7] is None


def test_order_identity_and_pending_or_poisoned_inputs_remain_unresolved(monkeypatch):
    monkeypatch.setattr(s, "receipt", lambda _tx: None)
    client = Client()
    assert s.classify(client, WALLET, ROW, (None, None))[0] == "UNKNOWN"
    assert s.classify(client, WALLET, ROW, (None, None), rejected=(True, True)) == (
        "REJECTED",
        None,
        None,
        D(0),
        D(0),
        D(0),
        D(0),
        "pUSD",
        "pUSD",
    )
    assert s.classify(client, WALLET, ROW, (UP, None), rejected=(False, True))[0] == "EXPOSED"
    client.orders[UP] = client.orders[UP].model_copy(update={"maker_address": s.EXCHANGE})
    assert s.classify(client, WALLET, ROW, (UP, DOWN))[0] == "EXPOSED"
    client = Client()
    client.trades["11"][0] = client.trades["11"][0].model_copy(update={"status": "MINED"})
    assert s.classify(client, WALLET, ROW, (UP, DOWN))[0] == "RESERVED"
    client.trades["11"][0] = client.trades["11"][0].model_copy(update={"status": "FAILED"})
    assert s.classify(client, WALLET, ROW, (UP, DOWN))[0] == "EXPOSED"


def test_receipt_requires_canonical_finalized_polygon_block(monkeypatch):
    values = {
        "eth_chainId": "0x89",
        "eth_getTransactionReceipt": {
            "status": "0x1",
            "transactionHash": TX,
            "blockHash": BLOCK,
            "blockNumber": "0xf",
        },
        "finalized": {"number": "0x10"},
        "0xf": {"hash": BLOCK},
    }

    class RPC:
        def __init__(self, **opts):
            assert opts["trust_env"] is opts["follow_redirects"] is False

        def __enter__(self):
            return self

        def __exit__(self, *_):
            return None

        def post(self, url, *, json):
            assert url == "https://polygon.drpc.org"
            k = json["params"][0] if json["method"] == "eth_getBlockByNumber" else json["method"]
            return SimpleNamespace(raise_for_status=lambda: None, json=lambda: {"id": 1, "result": values[k]})

    monkeypatch.setattr(s.httpx, "Client", RPC)
    assert s.receipt(TX) is values["eth_getTransactionReceipt"]
    values["finalized"] = {"number": "0xe"}
    assert s.receipt(TX) is None
    values["finalized"] = {"number": "0xf"}
    values["0xf"] = {"hash": TX}
    assert s.receipt(TX) is None
    values["eth_chainId"] = "0x1"
    assert s.receipt(TX) is None
