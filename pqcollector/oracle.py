"""Optional licensed Chainlink 60-second TWAP through PolyBolt's data channel."""

from __future__ import annotations

import asyncio
import json
import os
import time
from datetime import UTC, datetime, timedelta

import websockets
from websockets.exceptions import WebSocketException

from .markets import now, number, stamp

CHANNEL = "price.crypto.twap"
URL = "wss://ws-live-v2.polymarket.com/ws"
SYMBOLS = {"btcusd": "BTC", "ethusd": "ETH"}


class OracleCache:
    def __init__(self):
        self.prices: dict[str, dict[datetime, dict]] = {"BTC": {}, "ETH": {}}

    def clear(self):
        for series in self.prices.values():
            series.clear()

    def ingest(self, store, frame, received):
        if frame.get("channel") != CHANNEL or frame.get("v") != 1:
            raise ValueError("UNRECOGNIZED_ORACLE_CHANNEL")
        payload = frame["payload"]
        if payload.get("source") != "chainlink" or payload.get("window_seconds") != 60:
            raise ValueError("UNVERIFIED_ORACLE_SOURCE")
        asset = SYMBOLS[payload["symbol"]]
        seq = int(frame["seq"])
        points = payload["data"] if frame.get("snapshot") is True else [payload]
        series = self.prices[asset]
        for point in points:
            observed = datetime.fromtimestamp(int(point["timestamp"]) / 1000, UTC)
            value = point["full_accuracy_value"]
            if not isinstance(value, str) or number(value) <= 0:
                raise ValueError("INVALID_ORACLE_VALUE")
            lag = (received - observed).total_seconds()
            flags = ["SNAPSHOT_HISTORY"] if frame.get("snapshot") else []
            if lag < 0:
                flags.append("CLOCK_AHEAD")
            data = {
                "asset": asset,
                "price": value,
                "source": "chainlink",
                "twap_window": 60,
                "is_snapshot": bool(frame.get("snapshot")),
                "raw_point": point,
                "channel": CHANNEL,
                "sequence": seq,
                "envelope_time": frame["ts"],
            }
            event_id = store.event(
                "polybolt_chainlink",
                "ORACLE",
                None,
                observed,
                received,
                data,
                flags,
                identity=[asset, observed, value],
                sequence=seq,
            )
            if observed > received or observed < received - timedelta(minutes=20):
                continue
            series[observed] = {"price": value, "received_at": received, "event_id": event_id}
        cutoff = received - timedelta(minutes=20)
        for old in [ts for ts in series if ts < cutoff]:
            del series[old]

    def enrich(self, market, received):
        if not market["rule_verified"] or market["twap_window"] != 60:
            return {}
        series = self.prices[market["asset"]]
        if not series:
            return {}
        latest = max(series)
        start = stamp(market["start_time"])
        updated = {}
        if start in series and series[start]["received_at"] <= received and market.get("reference_price") is None:
            updated["reference_price"] = series[start]["price"]
            updated["reference_price_source"] = "chainlink:opening_twap"
            updated["reference_event_id"] = series[start]["event_id"]
        if series[latest]["received_at"] <= received and 0 <= (received - latest).total_seconds() <= 3:
            tick = series[latest]
            updated.update(
                oracle_source="chainlink",
                oracle_price=tick["price"],
                oracle_window=60,
                oracle_event_time=latest.isoformat(),
                oracle_received_at=tick["received_at"].isoformat(),
                oracle_event_id=tick["event_id"],
            )
        return updated


async def licensed_stream(store, cache: OracleCache):
    credentials = {
        name: os.getenv(env)
        for name, env in (
            ("apiKey", "POLYBOLT_API_KEY"),
            ("secret", "POLYBOLT_API_SECRET"),
            ("passphrase", "POLYBOLT_PASSPHRASE"),
        )
    }
    key = os.getenv("CHAINLINK_API_KEY")
    if not all(credentials.values()) or not key:
        store.status("chainlink", "UNAVAILABLE", "Licensed PolyBolt/Chainlink credentials are not configured")
        return
    delay = 1
    try:
        while True:
            try:
                async with websockets.connect(URL, open_timeout=12, close_timeout=2) as ws:
                    await ws.send(
                        json.dumps(
                            {"op": "auth", "rid": "research-auth", "auth": credentials, "chainlink_api_key": key}
                        )
                    )
                    auth = json.loads(await asyncio.wait_for(ws.recv(), 10))
                    if auth.get("op") != "authed":
                        if auth.get("code") == "auth_unavailable":
                            raise TimeoutError("AUTH_UNAVAILABLE")
                        store.status("chainlink", "UNAVAILABLE", "Data subscription authorization rejected")
                        return
                    await ws.send(
                        json.dumps(
                            {
                                "op": "subscribe",
                                "rid": "research-twap",
                                "subscriptions": [
                                    {
                                        "channel": CHANNEL,
                                        "filter": {
                                            "symbol": symbol,
                                            "provider": "chainlink",
                                            "window_seconds": 60,
                                        },
                                    }
                                    for symbol in SYMBOLS
                                ],
                            }
                        )
                    )
                    last_seq = None
                    last_data = time.monotonic()
                    while True:
                        frame = json.loads(await asyncio.wait_for(ws.recv(), 25))
                        if frame.get("op") == "error":
                            store.status("chainlink", "UNAVAILABLE", "Data subscription rejected")
                            return
                        if frame.get("op") == "subscribed" and frame.get("provider") not in {None, "chainlink"}:
                            raise ValueError("UNVERIFIED_ORACLE_SOURCE")
                        if frame.get("channel") == CHANNEL and "payload" in frame:
                            seq = int(frame["seq"])
                            if (last_seq is not None and seq != last_seq + 1) or frame.get("dropped", 0):
                                raise ValueError("ORACLE_SEQUENCE_GAP")
                            with store.db.transaction():
                                cache.ingest(store, frame, now())
                                store.status("chainlink", "ONLINE")
                            last_seq = seq
                            last_data = time.monotonic()
                            delay = 1
                        if time.monotonic() - last_data > 25:
                            raise TimeoutError("ORACLE_TIMEOUT")
            except (WebSocketException, OSError, TimeoutError, KeyError, ValueError, TypeError) as exc:
                cache.clear()
                code = getattr(getattr(exc, "response", None), "status_code", None)
                if code in {401, 403, 451}:
                    store.status("chainlink", "UNAVAILABLE", f"HTTP {code}")
                    return
                # Never persist server-provided text or credential-bearing frames.
                reason = type(exc).__name__
                at = now()
                store.event(
                    "polybolt_chainlink", "DATA_QUALITY", None, at, at, {"reason": reason}, ["ORACLE_FEED_GAP"]
                )
                store.status("chainlink", "DEGRADED", reason)
                await asyncio.sleep(delay)
                delay = min(delay * 2, 60)
    finally:
        cache.clear()
