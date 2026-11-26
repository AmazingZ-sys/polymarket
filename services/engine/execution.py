"""Account-locked, transactional signal, matching, settlement and risk processing."""

from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
from decimal import Decimal, DecimalException

from psycopg.types.json import Jsonb

from .model import Book, dec, match, opportunity, reserve
from .research import observe


def first(conn, sql, args=()):
    return conn.execute(sql, args).fetchone()


def validated(event, at):
    try:
        return Book.parse(event, at) if event and event["kind"] == "BOOK" else None
    except (KeyError, TypeError, ValueError, DecimalException):
        return None


def latest_book(conn, market, side, at, seq, after):
    # A gap or a resolution invalidates both cached sides until the next snapshot.
    row = first(
        conn,
        """SELECT * FROM engine_events WHERE market_id=%s AND seq<=%s
           AND received_at<=%s AND received_at>=%s AND
           ((kind='BOOK' AND data->>'side'=%s) OR kind IN ('GAP','RESOLUTION'))
           ORDER BY seq DESC LIMIT 1""",
        (market, seq, at, after, side),
    )
    return validated(row, at)


def post(conn, account, key, why, av, fr, ts, order=None, fill=None):
    conn.execute("SELECT pq_post(%s,%s,%s,%s,%s,%s,%s,%s)", (account, key, why, av, fr, ts, order, fill))


def halt(conn, a, reason, ts, market=None, evidence=None):
    if a["status"] not in {"RUNNING", "STARTING"}:
        return
    conn.execute("SELECT pq_cancel(%s,%s,%s)", (a["id"], reason, ts))
    conn.execute("UPDATE paper_accounts SET status='RISK_HALTED' WHERE id=%s", (a["id"],))
    conn.execute(
        "INSERT INTO risk_events(account_id,market_id,reason,evidence,created_at) VALUES(%s,%s,%s,%s,%s)",
        (a["id"], market, reason, Jsonb(evidence or {}), ts),
    )
    conn.execute(
        """INSERT INTO paper_sessions(account_id,from_status,status,actor,reason,created_at)
                  VALUES(%s,%s,'RISK_HALTED','engine',%s,%s)""",
        (a["id"], a["status"], reason, ts),
    )
    a["status"] = "RISK_HALTED"


def reconcile(conn, aid):
    row = first(
        conn,
        """SELECT a.available_balance=coalesce(l.av,0) AND a.frozen_balance=coalesce(l.fr,0)
       AND a.frozen_balance=(SELECT coalesce(sum(reserved),0) FROM paper_orders WHERE account_id=a.id)
       AND a.available_balance+a.frozen_balance=a.initial_balance+a.realized_pnl-
        (SELECT coalesce(sum(cost),0) FROM paper_positions WHERE account_id=a.id AND status='OPEN') AS ok
       FROM paper_accounts a CROSS JOIN LATERAL (SELECT sum(available_delta) av,sum(frozen_delta) fr
       FROM balance_ledger WHERE account_id=a.id) l WHERE a.id=%s""",
        (aid,),
    )
    return row and row["ok"]


def budget(conn, a, asset, market, ts):
    exposures = first(
        conn,
        """SELECT coalesce(sum(cost),0) total,
        coalesce(sum(cost) FILTER(WHERE asset=%s),0) asset,
        coalesce(sum(cost) FILTER(WHERE market_id=%s),0) market,
        coalesce(bool_and(fresh),true) fresh FROM (
        SELECT cost,asset,market_id,coalesce(mark_time>=%s-interval '3 seconds',false) fresh
        FROM paper_positions WHERE account_id=%s AND status='OPEN' UNION ALL
        SELECT reserved,asset,market_id,true FROM paper_orders WHERE account_id=%s AND reserved>0) p""",
        (asset, market, ts, a["id"], a["id"]),
    )
    cfg, equity = a["config"], a["equity"]
    if not exposures["fresh"]:
        return Decimal(0)
    return max(
        Decimal(0),
        min(
            a["available_balance"],
            equity * dec(cfg["risk_per_trade"]),
            equity * dec(cfg["market_cap"]) - exposures["market"],
            equity * dec(cfg["asset_cap"]) - exposures["asset"],
            equity * dec(cfg["portfolio_cap"]) - exposures["total"],
        ),
    )


def signal(conn, a, e, book):
    cfg, sid, ts = a["config"], a["strategy_id"], e["received_at"]
    book = replace(book, rate=book.rate * dec(cfg.get("cost_multiplier", "1")))
    if book.data["asset"] not in cfg["assets"] or book.data["timeframe"] not in cfg["timeframes"]:
        return
    # One probability observation per market/minute/strategy bounds passive accounts.
    if sid != "S01" and (
        book.side != "UP"
        or first(
            conn,
            """SELECT 1 FROM strategy_signals WHERE account_id=%s AND market_id=%s
        AND strategy_id=%s AND created_at>=date_trunc('minute',%s::timestamptz) LIMIT 1""",
            (a["id"], e["market_id"], sid, ts),
        )
    ):
        return
    peer = None
    if sid == "S04":
        peer = first(
            conn,
            """SELECT event_id,market_id,evidence->>'market_probability' p FROM strategy_signals
          WHERE account_id=%s AND market_id<>%s AND evidence->'book'->>'asset'=%s AND
          evidence->'book'->>'timeframe'<>%s AND created_at BETWEEN %s-interval '3 seconds' AND %s
          ORDER BY created_at DESC LIMIT 1""",
            (a["id"], e["market_id"], book.data["asset"], str(book.data["timeframe"]), ts, ts),
        )
    reason, evidence = observe(sid, book, cfg, peer)
    pair: list[Book] = []
    limits: list[Decimal] = []
    qty = Decimal(0)
    if sid == "S01":
        if first(
            conn,
            "SELECT 1 FROM paper_orders WHERE account_id=%s AND market_id=%s LIMIT 1",
            (a["id"], e["market_id"]),
        ):
            return
        other = latest_book(
            conn, e["market_id"], "DOWN" if book.side == "UP" else "UP", ts, e["seq"], a["fresh_after"]
        )
        pair = [book, replace(other, rate=other.rate * dec(cfg.get("cost_multiplier", "1")))] if other else [book]
        qty, limits, reason = opportunity(pair, cfg, budget(conn, a, book.data["asset"], e["market_id"], ts))
        evidence["books"] = [{"event_id": b.event_id, "book": b.data} for b in pair]
        evidence["quantity"] = str(qty)
        evidence["limits"] = list(map(str, limits))
        if len(pair) == 2:
            evidence["displayed_edge_per_share"] = str(1 - sum(b.asks[0][0] for b in pair))
    row = first(
        conn,
        """INSERT INTO strategy_signals(account_id,market_id,event_id,strategy_id,strategy_version,config_hash,
      action,reason,evidence,created_at) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING RETURNING id""",
        (
            a["id"],
            e["market_id"],
            e["event_id"],
            sid,
            a["strategy_version"],
            a["config_hash"],
            "PAIR" if qty else "OBSERVE",
            reason,
            Jsonb(evidence),
            ts,
        ),
    )
    if not row or not qty or a["mode"] == "SHADOW":
        return
    for b, limit in zip(pair, limits):
        locked = reserve(qty, limit, b.rate)
        order = first(
            conn,
            """INSERT INTO paper_orders(account_id,signal_id,market_id,asset,side,status,quantity,
             limit_price,reserved,rule_version,created_at,arrival_time,expires_at)
             VALUES(%s,%s,%s,%s,%s,'PENDING',%s,%s,%s,%s,%s,%s,%s) RETURNING id""",
            (
                a["id"],
                row["id"],
                e["market_id"],
                b.data["asset"],
                b.side,
                qty,
                limit,
                locked,
                b.rule,
                ts,
                ts + timedelta(milliseconds=int(dec(cfg["latency_ms"]) * dec(cfg.get("latency_multiplier", "1")))),
                b.close,
            ),
        )
        post(conn, a["id"], "reserve/" + str(order["id"]), "RESERVE", -locked, locked, ts, order["id"])


def execute_due(conn, a, ts, seq):
    for o in conn.execute(
        """SELECT * FROM paper_orders WHERE account_id=%s AND status='PENDING'
                              AND arrival_time<=%s ORDER BY arrival_time,id FOR UPDATE""",
        (a["id"], ts),
    ).fetchall():
        arrival, reserved, oid = o["arrival_time"], o["reserved"], o["id"]
        book = latest_book(conn, o["market_id"], o["side"], arrival, seq, max(a["fresh_after"], o["created_at"]))
        reason, status, fillq = "UNVERIFIABLE", "CANCELLED", Decimal(0)
        conn.execute("UPDATE paper_orders SET status='OPEN' WHERE id=%s", (oid,))
        if arrival >= o["expires_at"]:
            reason, status = "MARKET_CLOSED", "EXPIRED"
        elif book and book.received > o["created_at"] and book.rule == o["rule_version"]:
            book = replace(book, rate=book.rate * dec(a["config"].get("cost_multiplier", "1")))
            fill = match(book, a["config"], o["quantity"], o["limit_price"])
            cost = fill.notional + fill.fees
            if fill.quantity > 0 and cost <= reserved:
                fillq = fill.quantity
                status = "FILLED" if fillq == o["quantity"] else "PARTIALLY_FILLED"
                reason = "IOC_COMPLETE" if status == "FILLED" else "IOC_REMAINDER_CANCELLED"
                fid = first(
                    conn,
                    """INSERT INTO paper_fills(account_id,order_id,market_id,side,quantity,price,notional,fee,
                  slippage,event_id,evidence,created_at) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id""",
                    (
                        a["id"],
                        oid,
                        o["market_id"],
                        o["side"],
                        fillq,
                        fill.notional / fillq,
                        fill.notional,
                        fill.fees,
                        fill.slippage,
                        book.event_id,
                        Jsonb(
                            {
                                "book": book.data,
                                "levels": fill.levels,
                                "config": a["config"],
                                "arrival_time": arrival.isoformat(),
                                "observed": book.received.isoformat(),
                            }
                        ),
                        arrival,
                    ),
                )["id"]
                post(conn, a["id"], "fill/" + str(oid), "PURCHASE", 0, -fill.notional, arrival, oid, fid)
                post(conn, a["id"], "fee/" + str(oid), "FEE", 0, -fill.fees, arrival, oid, fid)
                conn.execute(
                    """INSERT INTO paper_positions(account_id,market_id,asset,side,quantity,cost,rule_version,
                    mark_price,mark_time,created_at) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                    (
                        a["id"],
                        o["market_id"],
                        o["asset"],
                        o["side"],
                        fillq,
                        cost,
                        book.rule,
                        book.bids[0][0],
                        book.received,
                        arrival,
                    ),
                )
                conn.execute("UPDATE paper_accounts SET fees=fees+%s WHERE id=%s", (fill.fees, a["id"]))
                reserved -= cost
            else:
                reason = "NO_LIQUIDITY_AT_LIMIT" if not fill.quantity else "FEE_BUDGET_EXCEEDED"
        post(conn, a["id"], "release/" + str(oid), "RELEASE", reserved, -reserved, arrival, oid)
        conn.execute(
            "UPDATE paper_orders SET status=%s,reason=%s,reserved=0,filled_quantity=%s WHERE id=%s",
            (status, reason, fillq, oid),
        )


def settle(conn, a, e):
    p = conn.execute(
        "SELECT * FROM paper_positions WHERE account_id=%s AND market_id=%s AND status='OPEN'",
        (a["id"], e["market_id"]),
    ).fetchall()
    if not p:
        return
    d, ts = e["data"], e["received_at"]
    if (
        e["quality_flags"]
        or d.get("verified") is not True
        or d.get("outcome") not in {"UP", "DOWN"}
        or not d.get("source")
        or any(d.get("rule_version") != pos["rule_version"] for pos in p)
    ):
        halt(conn, a, "SETTLEMENT_UNVERIFIED", ts, e["market_id"], {"event_id": e["event_id"]})
        return
    payout = sum(pos["quantity"] for pos in p if pos["side"] == d["outcome"])
    cost = sum(pos["cost"] for pos in p)
    conn.execute(
        """INSERT INTO paper_settlements(account_id,market_id,outcome,payout,cost,realized_pnl,source,rule_version,
      event_id,created_at) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
        (
            a["id"],
            e["market_id"],
            d["outcome"],
            payout,
            cost,
            payout - cost,
            d["source"],
            d["rule_version"],
            e["event_id"],
            ts,
        ),
    )
    post(conn, a["id"], "settle/" + e["market_id"], "SETTLEMENT", payout, 0, ts)
    conn.execute(
        "UPDATE paper_positions SET status='SETTLED' WHERE account_id=%s AND market_id=%s",
        (a["id"], e["market_id"]),
    )
    conn.execute(
        "UPDATE paper_accounts SET realized_pnl=realized_pnl+%s,consecutive_losses=CASE WHEN %s<0 THEN consecutive_losses+1 ELSE 0 END WHERE id=%s",
        (payout - cost, payout - cost, a["id"]),
    )
    conn.execute(
        """INSERT INTO trade_attributions(account_id,fill_id,market_id,reason,net_pnl,evidence,created_at)
        SELECT f.account_id,f.id,f.market_id,CASE WHEN o.status='PARTIALLY_FILLED' THEN 'PARTIAL_FILL_OBSERVED'
        WHEN f.side<>%s THEN 'LOSING_OUTCOME' ELSE 'WINNING_OUTCOME' END,
        (CASE WHEN f.side=%s THEN f.quantity ELSE 0 END)-f.notional-f.fee,
        jsonb_build_object('resolution_event',%s::text,'book_event',f.event_id,'fee',f.fee::text,
        'slippage',f.slippage::text,'fill_status',o.status,'causal_attribution','UNKNOWN'),%s
        FROM paper_fills f JOIN paper_orders o ON o.id=f.order_id WHERE f.account_id=%s AND f.market_id=%s""",
        (d["outcome"], d["outcome"], e["event_id"], ts, a["id"], e["market_id"]),
    )


def mark(conn, a, ts):
    row = first(
        conn,
        """SELECT available_balance+frozen_balance+
        coalesce((SELECT sum(quantity*coalesce(mark_price,0)) FROM paper_positions WHERE account_id=a.id AND status='OPEN'),0)
        AS equity,NOT EXISTS(SELECT FROM paper_positions WHERE account_id=a.id AND status='OPEN'
        AND (mark_time IS NULL OR mark_time<%s-interval '3 seconds')) AS fresh FROM paper_accounts a WHERE id=%s""",
        (ts, a["id"]),
    )
    eq = row["equity"]
    peak = max(a["peak_equity"], eq)
    dd = (peak - eq) / peak
    opening = a["day_open"] if a["equity_day"] in (None, ts.date()) else a["equity"]
    max_dd = max(dd, a["max_drawdown"])
    changed = eq != a["equity"] or a["equity_day"] != ts.date()
    conn.execute(
        """UPDATE paper_accounts SET equity=%s,peak_equity=%s,max_drawdown=%s,day_open=%s,equity_day=%s
        WHERE id=%s""",
        (eq, peak, max_dd, opening, ts.date(), a["id"]),
    )
    if changed:
        conn.execute(
            "INSERT INTO equity_snapshots(account_id,equity,drawdown,quality,created_at) VALUES(%s,%s,%s,%s,%s)",
            (a["id"], eq, dd, "OBSERVED" if row["fresh"] else "STALE_MARK", ts),
        )
    a.update(equity=eq, peak_equity=peak, max_drawdown=max_dd, day_open=opening, equity_day=ts.date())
    if dd >= dec(a["config"]["max_drawdown"]) or opening - eq >= opening * dec(a["config"]["daily_loss"]):
        halt(conn, a, "EQUITY_LIMIT", ts, evidence={"equity": str(eq), "drawdown": str(dd)})


def process(conn, a, e, *, allow_signals=True):
    ts, mid = e["received_at"], e["market_id"]
    timely = a["last_seen"] is None or ts >= a["last_seen"]
    if not timely:
        ts = a["last_seen"]
    if a["status"] == "RUNNING" and timely:
        execute_due(conn, a, ts, e["seq"])
    if e["kind"] == "RESOLUTION":
        settle(conn, a, e)
    elif e["kind"] == "GAP":
        impacted = bool(
            mid
            and first(
                conn,
                """SELECT 1 FROM paper_positions WHERE account_id=%s AND market_id=%s AND status='OPEN'
          UNION ALL SELECT 1 FROM paper_orders WHERE account_id=%s AND market_id=%s AND status='PENDING'
               UNION ALL SELECT 1 FROM strategy_signals WHERE account_id=%s AND market_id=%s
               AND created_at BETWEEN %s-interval '10 seconds' AND %s LIMIT 1""",
                (a["id"], mid, a["id"], mid, a["id"], mid, ts, ts),
            )
        )
        routine = e["data"].get("reason") == "SUBSCRIPTION_RESET"
        if impacted and routine:
            conn.execute("SELECT pq_cancel(%s,'BOOK_RESYNC',%s)", (a["id"], ts))
        elif impacted:
            halt(conn, a, "MARKET_DATA_GAP", ts, mid, {"event_id": e["event_id"]})
    elif e["kind"] == "BOOK" and timely and allow_signals:
        b = validated(e, ts)
        if (
            b
            and ts >= a["fresh_after"]
            and b.data["asset"] in a["config"]["assets"]
            and b.data["timeframe"] in a["config"]["timeframes"]
        ):
            a["last_book_at"] = ts
            conn.execute("UPDATE paper_accounts SET last_book_at=%s WHERE id=%s", (ts, a["id"]))
            conn.execute(
                "UPDATE paper_positions SET mark_price=%s,mark_time=%s WHERE account_id=%s AND market_id=%s AND side=%s AND status='OPEN'",
                (b.bids[0][0], ts, a["id"], mid, b.side),
            )
            mark(conn, a, ts)
            if a["status"] == "RUNNING":
                a["available_balance"] = first(
                    conn, "SELECT available_balance FROM paper_accounts WHERE id=%s", (a["id"],)
                )["available_balance"]
                signal(conn, a, e, b)
                for member in a["config"].get("strategies", []):
                    # Account-level exposure and simulation assumptions apply uniformly.
                    params = {
                        k: v for k, v in member["parameters"].items() if k in {"minimum_edge", "tail_seconds"}
                    }
                    child = {
                        **a,
                        "strategy_id": member["strategy_id"],
                        "strategy_version": member["strategy_version"],
                        "config": {**a["config"], **params},
                    }
                    signal(conn, child, e, b)
    if e["kind"] in {"RESOLUTION", "GAP"}:
        mark(conn, a, ts)
    conn.execute("UPDATE paper_accounts SET last_seq=%s,last_seen=%s WHERE id=%s", (e["seq"], ts, a["id"]))
    a.update(last_seq=e["seq"], last_seen=ts)
