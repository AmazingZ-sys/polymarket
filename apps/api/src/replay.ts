import type { PoolClient } from "pg";
import { z } from "zod";
import { ApiError, db } from "./db.js";

const assets = z.array(z.enum(["BTC", "ETH"])).min(1).max(2);
const timeframes = z.array(z.union([z.literal(5), z.literal(15)])).min(1).max(2);
export const period = z.object({
  start_time: z.iso.datetime({ offset: true }),
  end_time: z.iso.datetime({ offset: true }),
});

export function checkPeriod(start: string, end: string) {
  if (Date.parse(start) >= Date.parse(end) || Date.parse(end) > Date.now())
    throw new ApiError(400, "PERIOD", "请选择已结束的有效时间段");
}

const selectedMarkets = `SELECT market_id FROM markets
  WHERE asset=ANY($1::text[]) AND timeframe=ANY($2::integer[])`;
const windowEvents = `FROM market_replay_events
  WHERE market_id IN (${selectedMarkets}) AND received_at BETWEEN $3 AND $4`;

export async function requireReplayEvents(
  tx: PoolClient, assets: string[], timeframes: number[], start: string, end: string,
) {
  const { rowCount } = await tx.query(`SELECT 1 ${windowEvents} LIMIT 1`, [assets, timeframes, start, end]);
  if (!rowCount)
    throw new ApiError(409, "NO_REPLAY_EVENTS", "所选资产、周期和时间范围内没有可回放的市场事件。请使用最近有数据的区间，或先采集、导入历史数据。");
}

export async function replayWindow(query: Record<string, string>) {
  const scope = [
    assets.parse((query.assets ?? "BTC,ETH").split(",")),
    timeframes.parse((query.timeframes ?? "5,15").split(",").map(Number)),
  ];
  if (query.start_time !== undefined || query.end_time !== undefined) {
    const { start_time, end_time } = period.parse(query);
    checkPeriod(start_time, end_time);
    const { rows: [row] } = await db.query(`SELECT count(*) AS n ${windowEvents}`, [...scope, start_time, end_time]);
    return { start_time, end_time, event_count: Number(row.n) };
  }

  // Anchor to persisted observations even when collection stopped days ago.
  // Split the last 15 minutes of the selected markets at gaps over one minute.
  const { rows: [row] } = await db.query(`
    WITH selected AS (${selectedMarkets}), latest AS (
      SELECT max(last_event.received_at) AS ts FROM selected m
      CROSS JOIN LATERAL (SELECT received_at FROM market_replay_events
        WHERE market_id=m.market_id AND received_at<=date_trunc('milliseconds',now())
        ORDER BY received_at DESC LIMIT 1) last_event
    ), observations AS (
      SELECT received_at, lag(received_at) OVER (ORDER BY received_at) AS previous
        FROM market_replay_events, latest
       WHERE market_id IN (SELECT market_id FROM selected)
         AND received_at BETWEEN date_trunc('milliseconds',ts)-interval '15 minutes' AND ts
    ), bounds AS (
      SELECT coalesce(max(received_at) FILTER (WHERE received_at-previous>interval '1 minute'),
                      min(received_at)) AS first_event, max(received_at) AS last_event
        FROM observations
    ), window_end AS (
      SELECT first_event, date_trunc('milliseconds', last_event + interval '999 microseconds') AS end_time
        FROM bounds WHERE first_event IS NOT NULL
    ), suggested AS (
      SELECT least(date_trunc('milliseconds',first_event),end_time-interval '1 millisecond') AS start_time,
             end_time FROM window_end
    )
    SELECT start_time, end_time, (SELECT count(*) FROM market_replay_events
      WHERE market_id IN (SELECT market_id FROM selected) AND received_at BETWEEN start_time AND end_time) AS n
      FROM suggested`, scope);
  return row ? { start_time: row.start_time, end_time: row.end_time, event_count: Number(row.n) } : null;
}
