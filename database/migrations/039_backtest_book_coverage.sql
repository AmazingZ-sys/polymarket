-- Basic replay coverage requires both outcome books for every known market
-- and at least one such market for each requested asset/timeframe pair.
CREATE OR REPLACE FUNCTION pq_refresh_backtest_dataset(request_id uuid) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public,pg_temp AS $$
DECLARE
    d public.backtest_datasets;
    represented_scopes bigint;
    requested_scopes bigint;
BEGIN
    SELECT * INTO STRICT d FROM public.backtest_datasets WHERE id=request_id FOR UPDATE;
    SELECT count(DISTINCT (a,t)) INTO requested_scopes
      FROM unnest(d.assets) a CROSS JOIN unnest(d.timeframes) t;

    WITH market_counts AS (
        SELECT m.market_id,m.asset,m.timeframe,count(e.seq) AS events,
               count(e.seq) FILTER (WHERE e.kind='GAP') AS gaps,
               count(*) FILTER (WHERE q.usable) AS books,
               count(DISTINCT e.data->>'side') FILTER (WHERE q.usable) AS sides,
               sum(pg_column_size(e.data)+256) AS bytes,
               min(e.received_at) AS first_observed,max(e.received_at) AS last_observed
          FROM public.markets m
          LEFT JOIN public.engine_events e ON e.market_id=m.market_id
              AND e.received_at>=d.start_time AND e.received_at<d.end_time
          CROSS JOIN LATERAL (SELECT
              e.kind='BOOK' AND cardinality(e.quality_flags)=0
              AND e.event_time BETWEEN e.received_at-interval '3 seconds' AND e.received_at
              AND e.received_at>=m.start_time AND e.received_at<least(m.end_time,m.order_acceptance_end_time)
              AND m.rule_verified AND e.data->>'rule_version'=m.settlement_rule_version
              AND e.data @> '{"rule_verified":true,"accepting_orders":true}'
              AND e.data->>'asset'=m.asset AND e.data->>'timeframe'=m.timeframe::text
              AND ((e.data->>'side'='UP' AND e.data->>'token_id'=m.up_token_id)
                   OR (e.data->>'side'='DOWN' AND e.data->>'token_id'=m.down_token_id))
              AND e.data#>>'{asks,0,0}' IS NOT NULL AND e.data#>>'{asks,0,1}' IS NOT NULL
              AND e.data#>>'{bids,0,0}' IS NOT NULL AND e.data#>>'{bids,0,1}' IS NOT NULL AS usable) q
         WHERE m.asset=ANY(d.assets) AND m.timeframe=ANY(d.timeframes)
           AND m.start_time<d.end_time AND m.end_time>d.start_time
         GROUP BY m.market_id
    )
    SELECT count(*),count(DISTINCT (asset,timeframe)),count(*) FILTER (WHERE events>0),
           count(*) FILTER (WHERE sides=2),coalesce(sum(books),0),coalesce(sum(gaps),0),
           coalesce(sum(events),0),coalesce(sum(bytes),0),min(first_observed),max(last_observed)
      INTO d.market_count,represented_scopes,d.observed_market_count,d.book_market_count,d.book_count,
           d.gap_count,d.event_count,d.estimated_bytes,d.first_event_at,d.last_event_at
      FROM market_counts;

    SELECT coalesce(array_agg(v.version ORDER BY v.first_seq),'{}') INTO d.dataset_versions
      FROM public.dataset_versions v WHERE EXISTS (
          SELECT 1 FROM public.engine_events e JOIN public.markets m USING (market_id)
           WHERE e.seq BETWEEN v.first_seq AND v.last_seq AND m.asset=ANY(d.assets)
             AND m.timeframe=ANY(d.timeframes) AND m.start_time<d.end_time AND m.end_time>d.start_time
             AND e.received_at>=d.start_time AND e.received_at<d.end_time
      );

    d.reason=CASE
        WHEN d.expires_at<=now() THEN 'REQUEST_EXPIRED'
        WHEN d.market_count=0 THEN 'NO_MARKETS'
        WHEN d.event_count=0 THEN 'NO_EVENTS'
        WHEN d.estimated_bytes>d.max_bytes THEN 'DATASET_QUOTA_EXCEEDED'
        WHEN d.gap_count>0 THEN 'MARKET_DATA_GAP'
        WHEN represented_scopes<requested_scopes THEN 'MISSING_MARKET_SCOPE'
        WHEN d.observed_market_count<d.market_count THEN 'MARKET_WITHOUT_EVENTS'
        WHEN d.book_market_count<d.market_count THEN 'MISSING_QUALIFIED_BOOK_PAIR'
        ELSE 'BASIC_REPLAY_COVERAGE' END;
    d.status=CASE
        WHEN d.reason IN ('REQUEST_EXPIRED','NO_MARKETS','NO_EVENTS') THEN 'DATA_UNAVAILABLE'
        WHEN d.reason='BASIC_REPLAY_COVERAGE' THEN 'BACKTEST_READY'
        ELSE 'DATA_INCOMPLETE' END;

    UPDATE public.backtest_datasets SET status=d.status,reason=d.reason,event_count=d.event_count,
        market_count=d.market_count,observed_market_count=d.observed_market_count,book_market_count=d.book_market_count,
        book_count=d.book_count,gap_count=d.gap_count,first_event_at=d.first_event_at,last_event_at=d.last_event_at,
        dataset_versions=d.dataset_versions,estimated_bytes=d.estimated_bytes,checked_at=now()
      WHERE id=d.id RETURNING * INTO d;
    RETURN to_jsonb(d);
END $$;

-- A previously checked request needs the stronger gate before its next run.
UPDATE backtest_datasets SET status='DATA_INCOMPLETE',reason='RECHECK_REQUIRED',checked_at=now()
 WHERE status='BACKTEST_READY';
