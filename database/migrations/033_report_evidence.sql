-- Enrich each newly generated report inside the same transaction as the period result.
CREATE FUNCTION pq_enrich_report() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE extra jsonb; lo timestamptz=NEW.start_time; hi timestamptz=NEW.end_time; aid uuid=NEW.account_id;
BEGIN
 WITH scope AS (SELECT id FROM paper_accounts WHERE mode='PAPER' AND (aid IS NULL OR id=aid)),
 settled AS (SELECT s.* FROM paper_settlements s JOIN scope a ON a.id=s.account_id WHERE s.created_at>=lo AND s.created_at<hi),
 streaks AS (SELECT account_id,realized_pnl,sum((realized_pnl>=0)::int) OVER(PARTITION BY account_id ORDER BY created_at,id) bucket FROM settled),
 losses AS (SELECT count(*) n FROM streaks WHERE realized_pnl<0 GROUP BY account_id,bucket),
 dwell AS (SELECT extract(epoch FROM (s.created_at-f.created_at)) seconds,f.fee FROM settled s JOIN paper_fills f USING(account_id,market_id)),
 evidence AS (SELECT DISTINCT ON(ss.account_id,ss.market_id) ss.account_id, ss.market_id,(ss.evidence->>'market_probability')::numeric p,
    (r.outcome='UP')::int y FROM strategy_signals ss JOIN scope a ON a.id=ss.account_id JOIN market_resolutions r USING(market_id)
    WHERE ss.created_at>=lo AND ss.created_at<=r.received_at AND r.received_at<hi
    AND ss.evidence->'book'->>'rule_version'=r.rule_version AND ss.evidence ? 'market_probability'
    ORDER BY ss.account_id,ss.market_id,ss.created_at,ss.id),
 bins AS (SELECT least(9,floor(10*p)::int) bin,count(*) n,avg(p) predicted,avg(y) frequency FROM evidence GROUP BY 1),
 last_cash AS (SELECT l.frozen_after,l.available_after FROM scope s JOIN LATERAL (SELECT frozen_after,available_after FROM balance_ledger
   WHERE account_id=s.id AND created_at<hi ORDER BY created_at DESC,id DESC LIMIT 1) l ON true)
 SELECT jsonb_build_object('settlement_gross_before_fees',((SELECT sum(realized_pnl) FROM settled)+(SELECT sum(fee) FROM dwell))::text,
 'max_consecutive_losses',CASE WHEN EXISTS(SELECT FROM settled) THEN coalesce((SELECT max(n) FROM losses),0) END,
 'average_holding_seconds',(SELECT avg(seconds)::text FROM dwell),'end_frozen',(SELECT sum(frozen_after)::text FROM last_cash),
 'end_available',(SELECT sum(available_after)::text FROM last_cash),
 'max_observed_drawdown',(SELECT max(e.drawdown)::text FROM equity_snapshots e JOIN scope s ON s.id=e.account_id WHERE e.created_at>=lo AND e.created_at<hi),
 'risk_events',(SELECT count(*) FROM risk_events e JOIN scope s ON s.id=e.account_id WHERE e.created_at>=lo AND e.created_at<hi),
 'observed_gaps',(SELECT count(*) FROM engine_events WHERE kind='GAP' AND received_at>=lo AND received_at<hi),
 'probability_samples',(SELECT count(*) FROM evidence),'brier',(SELECT avg((p-y)^2) FROM evidence),
 'log_loss',(SELECT -avg(y*ln(greatest(0.000000000000001,p))+(1-y)*ln(greatest(0.000000000000001,1-p))) FROM evidence),
 'ece',(SELECT sum(n*abs(predicted-frequency))/nullif(sum(n),0) FROM bins),
 'reliability',(SELECT coalesce(jsonb_agg(to_jsonb(bins) ORDER BY bin),'[]') FROM bins))
 INTO extra;
 NEW.result=NEW.result||extra;
 RETURN NEW;
END $$;
CREATE TRIGGER enrich_new_report BEFORE INSERT ON performance_reports FOR EACH ROW EXECUTE FUNCTION pq_enrich_report();
ALTER TABLE improvement_proposals ADD COLUMN automated_key text UNIQUE;
INSERT INTO scheduled_jobs(name,interval_seconds) VALUES('strategy_review',86400);
