-- Market-scoped observations used for replay window discovery and admission.
-- Keep the kinds and inclusive received_at bounds aligned with engine/worker.py.
CREATE VIEW market_replay_events AS
    SELECT market_id, received_at
      FROM engine_events
     WHERE market_id IS NOT NULL AND kind IN
           ('BOOK','GAP','RESOLUTION','ORACLE','REFERENCE','TRADE','METADATA','DATA_QUALITY');

CREATE INDEX engine_market_replay_time ON engine_events(market_id, received_at DESC)
    WHERE market_id IS NOT NULL AND kind IN
          ('BOOK','GAP','RESOLUTION','ORACLE','REFERENCE','TRADE','METADATA','DATA_QUALITY');
