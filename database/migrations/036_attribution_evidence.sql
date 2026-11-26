-- Read model binds the initiating strategy and the observed book to each settled fill.
CREATE VIEW attribution_evidence AS
SELECT a.*, s.strategy_id,s.strategy_version,s.event_id AS signal_event,
       f.event_id AS book_event,a.evidence->>'resolution_event' AS resolution_event,
       CASE WHEN e.event_id IS NULL THEN 'UNKNOWN'
            WHEN cardinality(e.quality_flags)>0 THEN 'FLAGGED' ELSE 'CLEAN' END AS data_quality,
       e.quality_flags
FROM trade_attributions a JOIN paper_fills f ON f.id=a.fill_id
JOIN paper_orders o ON o.id=f.order_id
JOIN strategy_signals s ON s.id=o.signal_id
LEFT JOIN engine_events e ON e.event_id=f.event_id;
CREATE INDEX attributions_loss_time ON trade_attributions(created_at,net_pnl) WHERE net_pnl<0;
