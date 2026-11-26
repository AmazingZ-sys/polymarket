from __future__ import annotations

import asyncio
import json
import logging
import time
from collections import defaultdict
from datetime import datetime
from uuid import uuid4

import httpx
import websockets
from websockets.exceptions import ConnectionClosed, InvalidHandshake

from .book import Book, frame_time, quality
from .config import CollectorConfig
from .markets import candidates, now, number, parse_market, stamp
from .oracle import OracleCache, licensed_stream
from .reference import trade_tape
from .store import Store

LOG = logging.getLogger("pqcollector")
WS = "wss://ws-subscriptions-clob.polymarket.com/ws/market"
GAMMA = "https://gamma-api.polymarket.com/markets/slug/"
KRAKEN = "https://api.kraken.com/0/public/Ticker"
BOOK_FIELDS = (
    "asset",
    "timeframe",
    "start_time",
    "end_time",
    "rule_version",
    "rule_verified",
    "accepting_orders",
    "minimum_order_size",
    "fee_rate",
    "fee_exponent",
    "fee_schedule_version",
    "tick_size",
    "reference_price",
    "twap_window",
    "order_acceptance_end_time",
    "resolution_source",
)


class Discovery:
    def __init__(self, store: Store, http: httpx.AsyncClient, config: CollectorConfig | None = None):
        self.store, self.http = store, http
        self.config = config if isinstance(config, CollectorConfig) else CollectorConfig.from_env()
        self.markets: dict[str, dict] = {}
        self.oracle = OracleCache()
        self.gate = asyncio.Semaphore(3)
        self.blocked_until = 0.0

    async def fetch(self, slug):
        async with self.gate:
            if time.monotonic() < self.blocked_until:
                return None
            try:
                response = await self.http.get(GAMMA + slug)
                received = now()
                if response.status_code == 404:
                    return None
                if response.status_code in {403, 451}:
                    self.blocked_until = float("inf")
                if response.status_code == 429:
                    self.blocked_until = time.monotonic() + max(30, int(response.headers.get("retry-after", "60")))
                response.raise_for_status()
                raw = response.json()
                if raw.get("slug") != slug:
                    raise ValueError("SLUG_MISMATCH")
                m = parse_market(raw, received)
                with self.store.db.transaction():
                    m = self.store.metadata(m, received)
                self.markets[m["market_id"]] = m
                return True
            except (httpx.HTTPError, ValueError, TypeError, KeyError) as exc:
                self.store.gap(None, "MARKET_FETCH_ERROR", "gamma", {"slug": slug, "reason": str(exc)[:240]})
                return False

    async def refresh(self):
        at = now()
        slugs = dict.fromkeys(
            candidates(at, self.config.market_offsets) + self.store.unsettled(self.config.unsettled_limit)
        )
        results = await asyncio.gather(*(self.fetch(slug) for slug in slugs))
        if any(results) and False not in results:
            self.store.status("gamma", "ONLINE")
        else:
            self.store.status("gamma", "DEGRADED", "Missing market metadata; inspect GAP observations")
        self.markets = {
            mid: m for mid, m in self.markets.items() if (now() - stamp(m["end_time"])).total_seconds() < 600
        }

    def subscriptions(self) -> dict[str, dict]:
        at = now()
        return {
            m["condition_id"]: m
            for m in self.markets.values()
            if m["accepting_orders"] and at < stamp(m["end_time"]) and m["market_status"] in {"UPCOMING", "ACTIVE"}
        }


class Frames:
    def __init__(self, store: Store, discovery: Discovery, config: CollectorConfig | None = None):
        self.store, self.discovery = store, discovery
        candidate = config if isinstance(config, CollectorConfig) else getattr(discovery, "config", None)
        self.config = candidate if isinstance(candidate, CollectorConfig) else CollectorConfig.from_env()
        self.books: dict[str, Book] = {}
        self.ticks: dict[str, str] = {}
        self.last_output: dict[str, float] = {}
        self.full_times: dict[str, datetime] = {}
        self.epoch = str(uuid4())
        self.last_health = 0.0
        self.last_raw: dict[str, float] = {}

    def _persist_raw(self, market_id: str, event_type: str, current: float) -> bool:
        if self.config.raw_sample_seconds == 0 or event_type in {"book", "last_trade_price", "tick_size_change"}:
            self.last_raw[market_id] = current
            return True
        previous = self.last_raw.get(market_id)
        if previous is None or current - previous >= self.config.raw_sample_seconds:
            self.last_raw[market_id] = current
            return True
        return False

    def accept(self, frame: dict, received: datetime, market: dict):
        mid = market["market_id"]
        event_type = frame.get("event_type")
        event_time = frame_time(frame)
        # Exact retransmissions hash to the original raw row. Derived snapshots
        # reference it, including initial snapshots used to recover after a gap.
        raw_persisted = self._persist_raw(mid, event_type, time.monotonic())
        raw_key = self.store.event("clob_ws", "RAW", mid, event_time, received, frame, persist=raw_persisted)
        inputs: dict[str, list | None] = {}
        if event_type == "book":
            inputs[frame["asset_id"]] = None
            if frame.get("tick_size") is not None:
                self.ticks[frame["asset_id"]] = str(number(frame["tick_size"]))
        elif event_type == "price_change":
            grouped = defaultdict(list)
            for update in frame["price_changes"]:
                grouped[update["asset_id"]].append(update)
            inputs.update(grouped)
        elif event_type == "last_trade_price":
            token = frame["asset_id"]
            if token not in (market["up_token_id"], market["down_token_id"]):
                raise ValueError("TOKEN_MISMATCH")
            if not 0 <= number(frame["price"]) <= 1 or number(frame["size"]) <= 0:
                raise ValueError("INVALID_TRADE")
            self.store.event(
                "clob_ws",
                "TRADE",
                mid,
                event_time,
                received,
                {
                    **frame,
                    "token_id": token,
                    "asset": market["asset"],
                    "outcome": "UP" if token == market["up_token_id"] else "DOWN",
                },
                quality(event_time, received, market),
                identity=raw_key,
            )
        elif event_type == "tick_size_change":
            token = frame["asset_id"]
            tick = number(frame["new_tick_size"])
            if not 0 < tick < 1:
                raise ValueError("INVALID_TICK_SIZE")
            self.ticks[token] = str(tick)
            self.store.gap(mid, "TICK_SIZE_CHANGE", "clob_ws", frame, received)
        for token, changes in inputs.items():
            if token not in (market["up_token_id"], market["down_token_id"]):
                raise ValueError("TOKEN_MISMATCH")
            book = self.books.setdefault(token, Book())
            # Joining a live channel can queue deltas that predate the initial
            # authoritative full image. Retain the original frame for audit.
            if changes is not None and token in self.full_times and event_time < self.full_times[token]:
                continue
            book.apply(frame, changes)
            if changes is None:
                self.full_times[token] = event_time
            # Reconstructed books are published at the configured cadence.
            # Full resets are always emitted so a replay can recover a book.
            current = time.monotonic()
            tail = (stamp(market["end_time"]) - received).total_seconds() <= self.config.book_tail_seconds
            interval = self.config.book_tail_interval_seconds if tail else self.config.book_publish_interval_seconds
            if changes is not None and current - self.last_output.get(token, 0) < interval:
                continue
            self.last_output[token] = current
            data = {k: market.get(k) for k in BOOK_FIELDS}
            data.update(
                book.snapshot(),
                token_id=token,
                side="UP" if token == market["up_token_id"] else "DOWN",
                tick_size=self.ticks.get(token, market["tick_size"]),
                raw_event_id=raw_key if raw_persisted else None,
            )
            data.update(self.discovery.oracle.enrich(market, received))
            self.store.event(
                "clob_ws",
                "BOOK",
                mid,
                event_time,
                received,
                data,
                quality(event_time, received, market),
                identity=[raw_key, token, self.epoch],
            )
        if time.monotonic() - self.last_health > 3:
            self.store.status("clob_ws", "ONLINE")
            self.last_health = time.monotonic()


async def read_connection(
    store: Store, discovery: Discovery, subscriptions: dict, config: CollectorConfig | None = None
):
    frames = Frames(store, discovery, config)
    tokens = sorted(t for m in subscriptions.values() for t in (m["up_token_id"], m["down_token_id"]))
    async with websockets.connect(WS, open_timeout=12, close_timeout=2, ping_interval=None, max_queue=256) as ws:
        await ws.send(json.dumps({"type": "market", "assets_ids": tokens}))
        last_ping = last_pong = time.monotonic()
        while True:
            current_markets = discovery.subscriptions()
            if not subscriptions.keys() <= current_markets.keys():
                return "MARKET_CLOSED"
            current = time.monotonic()
            if current - last_pong > 25:
                raise TimeoutError("HEARTBEAT_TIMEOUT")
            if current - last_ping >= 10:
                await ws.send("PING")
                last_ping = current
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=1)
            except TimeoutError:
                store.flush()
                continue
            received = now()
            if raw == "PONG":
                last_pong = current
                continue
            frames_in_message = json.loads(raw)
            if isinstance(frames_in_message, dict):
                frames_in_message = [frames_in_message]
            with store.frame():
                for frame in frames_in_message:
                    m = current_markets.get(frame.get("market"))
                    if m is None:
                        continue
                    try:
                        frames.accept(frame, received, m)
                    except (ValueError, KeyError, TypeError, OverflowError) as exc:
                        store.gap(
                            m["market_id"],
                            "INVALID_BOOK",
                            "clob_ws",
                            {"error": str(exc), "frame": frame},
                            received,
                        )
                        return "INVALID_BOOK"


async def stream_market(store: Store, discovery: Discovery, condition: str):
    delay = 1
    while market := discovery.subscriptions().get(condition):
        started = time.monotonic()
        try:
            reason = await read_connection(store, discovery, {condition: market}, discovery.config)
            if reason == "MARKET_CLOSED":
                return
        except (ConnectionClosed, InvalidHandshake, TimeoutError, OSError, ValueError) as exc:
            reason = type(exc).__name__
            status = getattr(getattr(exc, "response", None), "status_code", None)
            with store.db.transaction():
                store.gap(market["market_id"], reason)
                store.status("clob_ws", "DEGRADED", reason)
            if status in {401, 403, 451}:
                return
        if time.monotonic() - started > 15:
            delay = 1
        await asyncio.sleep(delay)
        delay = min(60, delay * 2)


async def stream(store: Store, discovery: Discovery):
    # One connection covers both outcome tokens. A market ending or requiring
    # a resync does not interrupt the other research scopes.
    running: dict[str, asyncio.Task] = {}
    try:
        while True:
            subs = discovery.subscriptions()
            for condition in list(running):
                task = running[condition]
                if task.done():
                    task.result()
                    if condition not in subs:
                        del running[condition]
            for condition in subs.keys() - running.keys():
                running[condition] = asyncio.create_task(stream_market(store, discovery, condition))
            await asyncio.sleep(1)
    finally:
        for task in running.values():
            task.cancel()
        await asyncio.gather(*running.values(), return_exceptions=True)
        for m in discovery.subscriptions().values():
            store.gap(m["market_id"], "SUBSCRIPTION_RESET")
        store.status("clob_ws", "OFFLINE", "Collector stopped")


async def discover_forever(discovery: Discovery):
    while True:
        await discovery.refresh()
        await asyncio.sleep(discovery.config.discovery_interval_seconds)


async def references(store: Store, http: httpx.AsyncClient, config: CollectorConfig | None = None):
    config = config or CollectorConfig.from_env()
    delay = config.reference_interval_seconds
    while True:
        try:
            res = await http.get(KRAKEN, params={"pair": "XBTUSD,ETHUSD"})
            at = now()
            res.raise_for_status()
            payload = res.json()
            if payload["error"]:
                raise ValueError("Kraken rejected quote request")
            with store.db.transaction():
                for asset, key in (("BTC", "XXBTZUSD"), ("ETH", "XETHZUSD")):
                    quote = payload["result"][key]
                    bid, ask, price = [number(quote[k][0]) for k in ("b", "a", "c")]
                    if min(bid, ask, price) <= 0 or ask < bid:
                        raise ValueError("INVALID_REFERENCE")
                    store.event(
                        "kraken",
                        "REFERENCE",
                        None,
                        at,
                        at,
                        {
                            "asset": asset,
                            "pair": f"{asset}/USD",
                            "price": str(price),
                            "bid": str(bid),
                            "ask": str(ask),
                            "last_size": quote["c"][1],
                            "raw": quote,
                        },
                        ["SOURCE_TIME_UNKNOWN"],
                    )
                store.status("kraken", "ONLINE")
            delay = config.reference_interval_seconds
        except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
            status = exc.response.status_code if isinstance(exc, httpx.HTTPStatusError) else None
            store.status("kraken", "UNAVAILABLE", str(exc)[:240])
            if status in (403, 451):
                return
            delay = min(120, 2 * delay)
            if status == 429 and isinstance(exc, httpx.HTTPStatusError):
                try:
                    delay = max(delay, int(exc.response.headers.get("retry-after", "60")))
                except ValueError:
                    delay = max(delay, 60)
        await asyncio.sleep(delay)


async def run(store: Store, seconds=None):
    config = CollectorConfig.from_env()
    store.configure_budget(
        config.daily_event_budget_bytes,
        config.min_free_bytes,
        config.deduplicate_metadata,
        config.max_disk_usage_bytes,
    )
    LOG.info("collector configuration %s", json.dumps(config.snapshot(), sort_keys=True))
    mode = store.db.execute("SELECT current_setting('polyquant.mode',true) AS mode").fetchone()
    if mode and mode["mode"] == "history":
        raise ValueError("Historical databases only accept offline research")
    lock = store.db.execute("SELECT pg_try_advisory_lock(7386101) AS ok").fetchone()
    if not lock or not lock["ok"]:
        raise RuntimeError("Another collector is active")
    for row in store.db.execute("SELECT market_id FROM markets WHERE end_time>now() AND accepting_orders"):
        store.gap(row["market_id"], "COLLECTOR_RESTART")
    async with httpx.AsyncClient(timeout=10) as client:
        discovery = Discovery(store, client, config)
        await discovery.refresh()
        tasks = [
            asyncio.create_task(c)
            for c in (
                discover_forever_after_delay(discovery),
                stream(store, discovery),
                references(store, client, config),
                licensed_stream(store, discovery.oracle),
            )
        ]
        if config.enable_reference_trades:
            tasks.extend(
                (
                    asyncio.create_task(trade_tape(store, client, "BTC", "XBTUSD")),
                    asyncio.create_task(trade_tape(store, client, "ETH", "ETHUSD")),
                )
            )
        else:
            LOG.info("collector reference trade tape disabled by profile=%s", config.profile)
        try:
            done, _ = await asyncio.wait(tasks, timeout=seconds, return_when=asyncio.FIRST_EXCEPTION)
            for task in done:
                task.result()
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)


async def discover_forever_after_delay(discovery):
    await asyncio.sleep(discovery.config.discovery_interval_seconds)
    await discover_forever(discovery)
