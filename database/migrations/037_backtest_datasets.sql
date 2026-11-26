-- A request records what historical coverage a backtest needs.  It never
-- claims that an upstream provider can supply history that has not been
-- observed or imported into this database.
CREATE TABLE backtest_datasets(
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    assets text[] NOT NULL CHECK (cardinality(assets)>0 AND assets <@ ARRAY['BTC','ETH']::text[]),
    timeframes integer[] NOT NULL CHECK (cardinality(timeframes)>0 AND timeframes <@ ARRAY[5,15]::integer[]),
    start_time timestamptz NOT NULL,
    end_time timestamptz NOT NULL CHECK(end_time>start_time),
    max_bytes bigint NOT NULL DEFAULT 536870912 CHECK(max_bytes BETWEEN 1048576 AND 1073741824),
    estimated_bytes bigint NOT NULL DEFAULT 0 CHECK(estimated_bytes>=0),
    expires_at timestamptz NOT NULL DEFAULT (now()+interval '7 days'),
    status text NOT NULL DEFAULT 'DATA_UNAVAILABLE' CHECK(status IN ('DATA_UNAVAILABLE','DATA_INCOMPLETE','BACKTEST_READY')),
    reason text NOT NULL DEFAULT 'NOT_CHECKED',
    event_count bigint NOT NULL DEFAULT 0 CHECK(event_count>=0),
    market_count bigint NOT NULL DEFAULT 0 CHECK(market_count>=0),
    observed_market_count bigint NOT NULL DEFAULT 0 CHECK(observed_market_count>=0),
    book_market_count bigint NOT NULL DEFAULT 0 CHECK(book_market_count>=0),
    book_count bigint NOT NULL DEFAULT 0 CHECK(book_count>=0),
    gap_count bigint NOT NULL DEFAULT 0 CHECK(gap_count>=0),
    first_event_at timestamptz,
    last_event_at timestamptz,
    dataset_versions text[] NOT NULL DEFAULT '{}',
    requested_by text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    checked_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE(assets,timeframes,start_time,end_time)
);
CREATE INDEX backtest_datasets_status ON backtest_datasets(status,checked_at DESC);

ALTER TABLE backtest_runs ADD COLUMN dataset_id uuid REFERENCES backtest_datasets;
CREATE INDEX backtest_runs_dataset ON backtest_runs(dataset_id) WHERE dataset_id IS NOT NULL;

CREATE FUNCTION pq_refresh_backtest_dataset(request_id uuid) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public,pg_temp AS $$
DECLARE
    d backtest_datasets;
    m_count bigint;
    observed_count bigint;
    book_markets bigint;
    books bigint;
    gaps bigint;
    events bigint;
    first_event timestamptz;
    last_event timestamptz;
    versions text[];
    next_status text;
    next_reason text;
BEGIN
    SELECT * INTO STRICT d FROM backtest_datasets WHERE id=request_id FOR UPDATE;
    SELECT count(*) INTO m_count
      FROM markets m
     WHERE m.asset=ANY(d.assets) AND m.timeframe=ANY(d.timeframes)
       AND m.start_time<d.end_time AND m.end_time>d.start_time;
    SELECT count(*), count(DISTINCT e.market_id),
           count(DISTINCT e.market_id) FILTER (WHERE e.kind='BOOK'),
           count(*) FILTER (WHERE e.kind='BOOK'),
           count(*) FILTER (WHERE e.kind='GAP'),
           min(e.received_at), max(e.received_at)
      INTO events,observed_count,book_markets,books,gaps,first_event,last_event
      FROM engine_events e JOIN markets m ON m.market_id=e.market_id
     WHERE m.asset=ANY(d.assets) AND m.timeframe=ANY(d.timeframes)
       AND e.received_at>=d.start_time AND e.received_at<d.end_time;
    SELECT coalesce(array_agg(DISTINCT v.version ORDER BY v.version),'{}') INTO versions
      FROM dataset_versions v JOIN engine_events e ON e.seq BETWEEN v.first_seq AND v.last_seq
                              JOIN markets m ON m.market_id=e.market_id
     WHERE m.asset=ANY(d.assets) AND m.timeframe=ANY(d.timeframes)
       AND e.received_at>=d.start_time AND e.received_at<d.end_time;

    SELECT coalesce(sum(pg_column_size(e.data)+256),0) INTO d.estimated_bytes
      FROM engine_events e JOIN markets m ON m.market_id=e.market_id
     WHERE m.asset=ANY(d.assets) AND m.timeframe=ANY(d.timeframes)
       AND e.received_at>=d.start_time AND e.received_at<d.end_time;

    IF d.expires_at<=now() THEN
        next_status='DATA_UNAVAILABLE';
        next_reason='REQUEST_EXPIRED';
    ELSIF m_count=0 OR events=0 THEN
        next_status='DATA_UNAVAILABLE';
        next_reason=CASE WHEN m_count=0 THEN 'NO_MARKETS' ELSE 'NO_EVENTS' END;
    ELSIF d.estimated_bytes>d.max_bytes THEN
        next_status='DATA_INCOMPLETE';
        next_reason='DATASET_QUOTA_EXCEEDED';
    ELSIF gaps>0 OR observed_count<m_count OR book_markets<m_count THEN
        next_status='DATA_INCOMPLETE';
        next_reason=CASE WHEN gaps>0 THEN 'MARKET_DATA_GAP'
                         WHEN observed_count<m_count THEN 'MARKET_WITHOUT_EVENTS'
                         ELSE 'MARKET_WITHOUT_BOOK' END;
    ELSE
        next_status='BACKTEST_READY';
        next_reason='COVERAGE_VERIFIED';
    END IF;

    UPDATE backtest_datasets SET status=next_status,reason=next_reason,event_count=events,
        market_count=m_count,observed_market_count=observed_count,book_market_count=book_markets,
        book_count=books,gap_count=gaps,first_event_at=first_event,last_event_at=last_event,
        dataset_versions=versions,estimated_bytes=d.estimated_bytes,checked_at=now()
      WHERE id=d.id
      RETURNING * INTO d;
    RETURN to_jsonb(d);
END $$;
REVOKE ALL ON FUNCTION pq_refresh_backtest_dataset(uuid) FROM PUBLIC;
