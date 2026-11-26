"""Supplemental trade evidence. Kraken prices never authorize settlement."""

from __future__ import annotations

import asyncio
import itertools
import math
from collections import deque
from datetime import UTC, datetime, timedelta

import httpx

from .markets import now, number

TRADES = "https://api.kraken.com/0/public/Trades"


async def trade_tape(store, http: httpx.AsyncClient, asset: str, pair: str):
    source = f"kraken_trades_{asset.lower()}"
    last = store.db.execute(
        """
        SELECT data->>'cursor' AS cursor FROM engine_events WHERE source=%s AND kind='CHECKPOINT'
        ORDER BY seq DESC LIMIT 1""",
        (source,),
    ).fetchone()
    cursor = last["cursor"] if last else None
    window: deque[tuple[datetime, float]] = deque()
    wait = 10
    while True:
        try:
            params = {"pair": pair, "count": "100"}
            if cursor:
                params["since"] = cursor
            response = await http.get(TRADES, params=params)
            at = now()
            response.raise_for_status()
            payload = response.json()
            if payload["error"]:
                raise ValueError("REFERENCE_TRADES_REJECTED")
            result = payload["result"]
            pair_key = "XXBTZUSD" if asset == "BTC" else "XETHZUSD"
            trades = result[pair_key]
            next_cursor = str(result["last"])
            with store.db.transaction():
                for item in trades:
                    price = number(item[0])
                    if price <= 0 or number(item[1]) <= 0:
                        raise ValueError("INVALID_TRADE")
                    ts = datetime.fromtimestamp(float(item[2]), UTC)
                    flags = []
                    if ts > at:
                        flags.append("CLOCK_AHEAD")
                    if window and ts < window[-1][0]:
                        flags.append("OUT_OF_ORDER")
                    if not flags:
                        window.append((ts, float(price)))
                        while window and (ts - window[0][0]) > timedelta(seconds=60):
                            window.popleft()
                    store.event(
                        source,
                        "REFERENCE_TRADE",
                        None,
                        ts,
                        at,
                        {
                            "asset": asset,
                            "pair": f"{asset}/USD",
                            "price": str(price),
                            "size": str(item[1]),
                            "trade_id": str(item[6]),
                            "side": item[3],
                            "raw": item,
                        },
                        flags,
                        identity=item[6],
                    )
                if len(window) > 1:
                    values = [v for _, v in window]
                    variance = sum(math.log(b / a) ** 2 for a, b in itertools.pairwise(values))
                    store.event(
                        source,
                        "REFERENCE_VOLATILITY",
                        None,
                        window[-1][0],
                        at,
                        {
                            "asset": asset,
                            "lookback_seconds": 60,
                            "observed_seconds": (window[-1][0] - window[0][0]).total_seconds(),
                            "trade_count": len(values),
                            "realized_log_volatility": math.sqrt(variance),
                        },
                        identity=[next_cursor, len(window)],
                    )
                store.event(source, "CHECKPOINT", None, at, at, {"cursor": next_cursor}, identity=next_cursor)
                store.status(source, "ONLINE")
            cursor = next_cursor
            wait = 2 if len(trades) >= 100 else 10
        except (httpx.HTTPError, ValueError, TypeError, IndexError, KeyError) as exc:
            status = exc.response.status_code if isinstance(exc, httpx.HTTPStatusError) else None
            store.status(source, "UNAVAILABLE", type(exc).__name__)
            if status in {403, 451}:
                return
            wait = min(120, max(10, wait * 2))
            if isinstance(exc, httpx.HTTPStatusError) and status == 429:
                retry = exc.response.headers.get("retry-after", "")
                if retry.isdigit():
                    wait = max(wait, int(retry))
        await asyncio.sleep(wait)
