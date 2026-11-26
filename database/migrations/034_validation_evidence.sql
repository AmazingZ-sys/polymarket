-- Evaluation amounts and statistical denominators use the same experiment window.
CREATE OR REPLACE FUNCTION pq_evaluate(p jsonb, reviewer text) RETURNS jsonb LANGUAGE plpgsql AS $$
DECLARE
 a paper_accounts; exp comparison_experiments; base backtest_runs; stress backtest_runs;
 ba paper_accounts; sa paper_accounts; result_row strategy_evaluations;
 lo timestamptz; hi timestamptz; operating_cost numeric; fill_samples bigint; market_samples bigint;
 valid_days int; paper_streak numeric; paper_markets bigint; paper_net numeric; paper_pf numeric;
 paper_dd numeric; unresolved bigint; ci numeric; cost_per_market numeric; eligible boolean; verdict text; m jsonb;
BEGIN
 SELECT * INTO STRICT a FROM paper_accounts WHERE id=(p->>'account_id')::uuid AND mode='PAPER';
 SELECT * INTO STRICT exp FROM comparison_experiments WHERE id=(p->>'experiment_id')::uuid AND a.id=ANY(account_ids);
 SELECT * INTO STRICT base FROM backtest_runs WHERE id=(p->>'backtest_id')::uuid AND status='COMPLETED';
 SELECT * INTO STRICT stress FROM backtest_runs WHERE id=(p->>'stress_backtest_id')::uuid AND status='COMPLETED' AND id<>base.id;
 SELECT * INTO STRICT ba FROM paper_accounts WHERE id=base.account_id AND mode='BACKTEST';
 SELECT * INTO STRICT sa FROM paper_accounts WHERE id=stress.account_id AND mode='BACKTEST';
 lo=(base.result->>'start_time')::timestamptz;
 hi=(base.result->>'end_time')::timestamptz;
 operating_cost=(p->>'cost_usd')::numeric;
 IF operating_cost IS NULL OR operating_cost<0 OR operating_cost::text IN ('NaN','Infinity','-Infinity') THEN RAISE EXCEPTION 'invalid operating cost'; END IF;
 IF (a.strategy_id,a.strategy_version,a.initial_balance,a.config-'hypothesis'-'cost_multiplier',coalesce((a.config->>'cost_multiplier')::numeric,1))
    IS DISTINCT FROM (ba.strategy_id,ba.strategy_version,ba.initial_balance,ba.config-'hypothesis'-'cost_multiplier',coalesce((ba.config->>'cost_multiplier')::numeric,1))
 OR (ba.strategy_id,ba.strategy_version,ba.initial_balance,ba.config-'cost_multiplier')
    IS DISTINCT FROM (sa.strategy_id,sa.strategy_version,sa.initial_balance,sa.config-'cost_multiplier')
 OR base.result->>'data_hash' IS NULL OR base.result->>'code_hash' IS NULL OR lo IS NULL OR hi IS NULL OR lo>=hi
 OR (base.result->>'data_hash',base.result->>'code_hash',lo,hi)
    IS DISTINCT FROM (stress.result->>'data_hash',stress.result->>'code_hash',(stress.result->>'start_time')::timestamptz,(stress.result->>'end_time')::timestamptz)
 OR coalesce((sa.config->>'cost_multiplier')::numeric,1)<1.5*coalesce((ba.config->>'cost_multiplier')::numeric,1)
 THEN RAISE EXCEPTION 'candidate and paired fee-stress replay inputs must match'; END IF;
 SELECT count(*),count(DISTINCT f.market_id) INTO fill_samples,market_samples FROM paper_fills f JOIN paper_settlements s USING(account_id,market_id)
 WHERE f.account_id=ba.id AND f.created_at BETWEEN lo AND hi AND s.created_at BETWEEN lo AND hi;
 -- Count a date only if each configured asset/timeframe has a quality-passing book within the frozen replay boundary.
 SELECT count(*) INTO valid_days FROM (
   SELECT (e.received_at AT TIME ZONE 'UTC')::date AS observation_day FROM engine_events e WHERE kind='BOOK' AND cardinality(quality_flags)=0
   AND e.seq BETWEEN base.first_seq AND base.boundary_seq AND e.received_at BETWEEN lo AND hi
   AND ba.config->'assets' ? (e.data->>'asset') AND ba.config->'timeframes' @> jsonb_build_array(e.data->'timeframe')
   GROUP BY 1 HAVING count(DISTINCT (e.data->>'asset',e.data->>'timeframe'))=
     jsonb_array_length(ba.config->'assets')*jsonb_array_length(ba.config->'timeframes')
 ) days;
 -- Session continuity is intersected with actual signal observations. A string of isolated dates does not qualify.
 WITH transitions AS (
  SELECT status,created_at,lead(created_at,1,hi) OVER(ORDER BY created_at,id) until_time
  FROM paper_sessions WHERE account_id=a.id AND created_at<=hi
 ), spans AS (
  SELECT greatest(lo,created_at,exp.created_at) begin_at, least(hi,until_time) end_at FROM transitions WHERE status='RUNNING'
 ), observed AS (
  SELECT spans.begin_at,spans.end_at,(s.created_at AT TIME ZONE 'UTC')::date AS observation_day,min(s.created_at) first_at,max(s.created_at) last_at
  FROM spans JOIN strategy_signals s ON s.account_id=a.id AND s.created_at>=begin_at AND s.created_at<end_at GROUP BY 1,2,3
 ), islands AS (
  SELECT *,observation_day-(row_number() OVER(PARTITION BY begin_at,end_at ORDER BY observation_day))::int series FROM observed
 ) SELECT coalesce(max(duration),0) INTO paper_streak FROM
  (SELECT extract(epoch FROM max(last_at)-min(first_at))/86400 duration FROM islands GROUP BY begin_at,end_at,series) runs;
 SELECT count(*),sum(realized_pnl)-operating_cost,sum(greatest(realized_pnl,0))/nullif(sum(greatest(-realized_pnl,0)),0)
 INTO paper_markets,paper_net,paper_pf FROM paper_settlements WHERE account_id=a.id AND created_at BETWEEN lo AND hi;
 SELECT max(drawdown) INTO paper_dd FROM equity_snapshots WHERE account_id=a.id AND created_at BETWEEN lo AND hi;
 SELECT count(*) INTO unresolved FROM paper_positions pos LEFT JOIN paper_settlements s USING(account_id,market_id)
 WHERE pos.account_id IN (a.id,ba.id,sa.id) AND pos.created_at<=hi AND (s.created_at IS NULL OR s.created_at>hi
    OR (pos.created_at<lo AND s.created_at>=lo));
 IF jsonb_typeof(base.result->'mean_pnl_daily_block_ci')='array' THEN ci=(base.result->'mean_pnl_daily_block_ci'->>0)::numeric; END IF;
 cost_per_market=operating_cost/nullif(market_samples,0);
 m=jsonb_build_object('protocol','initial-v2','start_time',lo,'end_time',hi,'out_of_sample_settled_fills',fill_samples,
  'out_of_sample_settled_markets',market_samples,'observed_utc_days',valid_days,'paper_days',paper_streak,
  'paper_settled_markets',paper_markets,'paper_net_after_cost',paper_net::text,'profit_factor',paper_pf::text,
  'max_drawdown',paper_dd::text,'bootstrap_ci',base.result->'mean_pnl_daily_block_ci','cost_per_settled_market',cost_per_market::text,
  'base_net_after_cost',((base.result->>'net_pnl')::numeric-operating_cost)::text,'stress_net_after_cost',((stress.result->>'net_pnl')::numeric-operating_cost)::text,
  'manual_operating_cost',operating_cost::text,'unresolved_or_cross_window_positions',unresolved,'experiment_registered_at',exp.created_at);
 eligible=valid_days>=90 AND hi-lo>=interval '90 days' AND fill_samples>=1000 AND paper_streak>=30 AND lo>=exp.created_at
   AND ci IS NOT NULL AND paper_markets>0 AND paper_pf IS NOT NULL AND paper_dd IS NOT NULL AND unresolved=0;
 verdict=CASE WHEN NOT coalesce(eligible,false) THEN 'INSUFFICIENT_DATA'
 WHEN paper_pf>1.3 AND paper_dd<=0.05 AND (base.result->>'max_drawdown')::numeric<=0.05
 AND paper_net>0 AND (m->>'base_net_after_cost')::numeric>0 AND (m->>'stress_net_after_cost')::numeric>0
 AND ci>cost_per_market AND p->'checks' @> '{"quality":true,"delay":true,"extreme":true,"multiple_testing":true,"independent_sample":true,"continuous_coverage":true}'
 THEN 'VALIDATION_PASSED' ELSE 'VALIDATION_FAILED' END;
 INSERT INTO strategy_evaluations(experiment_id,account_id,strategy_id,strategy_version,backtest_id,stress_backtest_id,status,evidence,metrics,checks,actor)
 VALUES(exp.id,a.id,a.strategy_id,a.strategy_version,base.id,stress.id,verdict,p->>'evidence',m,p->'checks',reviewer) RETURNING * INTO result_row;
 RETURN to_jsonb(result_row);
END $$;

-- Enrich each newly generated report inside the same transaction as the period result.
CREATE OR REPLACE FUNCTION pq_enrich_report() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE extra jsonb; lo timestamptz=NEW.start_time; hi timestamptz=NEW.end_time; aid uuid=NEW.account_id;
BEGIN
 WITH scope AS (SELECT id FROM paper_accounts WHERE mode='PAPER' AND (aid IS NULL OR id=aid)),
 settled AS (SELECT s.* FROM paper_settlements s JOIN scope a ON a.id=s.account_id WHERE s.created_at>=lo AND s.created_at<hi),
 streaks AS (SELECT account_id,realized_pnl,sum((realized_pnl>=0)::int) OVER(PARTITION BY account_id ORDER BY created_at,id) bucket FROM settled),
 losses AS (SELECT count(*) n FROM streaks WHERE realized_pnl<0 GROUP BY account_id,bucket),
 dwell AS (SELECT extract(epoch FROM (s.created_at-f.created_at)) seconds,f.fee FROM settled s JOIN paper_fills f USING(account_id,market_id)),
 evidence AS (SELECT DISTINCT ON(ss.market_id) ss.account_id, ss.market_id,(ss.evidence->>'market_probability')::numeric p,
    (r.outcome='UP')::int y FROM strategy_signals ss JOIN scope a ON a.id=ss.account_id JOIN market_resolutions r USING(market_id)
    WHERE ss.created_at>=lo AND ss.created_at<=r.received_at AND r.received_at<hi
    AND ss.evidence->'book'->>'rule_version'=r.rule_version AND ss.evidence ? 'market_probability'
    ORDER BY ss.market_id,ss.created_at,ss.id),
 bins AS (SELECT least(9,floor(10*p)::int) bin,count(*) n,avg(p) predicted,avg(y) frequency FROM evidence GROUP BY 1),
 last_cash AS (SELECT l.frozen_after,l.available_after FROM scope s JOIN LATERAL (SELECT frozen_after,available_after FROM balance_ledger
   WHERE account_id=s.id AND created_at<hi ORDER BY created_at DESC,id DESC LIMIT 1) l ON true)
 SELECT jsonb_build_object('settlement_gross_before_fees',((SELECT sum(realized_pnl) FROM settled)+(SELECT sum(fee) FROM dwell))::text,
 'max_consecutive_losses',CASE WHEN EXISTS(SELECT FROM settled) THEN coalesce((SELECT max(n) FROM losses),0) END,
 'average_holding_seconds',(SELECT avg(seconds)::text FROM dwell),'end_frozen',(SELECT sum(frozen_after)::text FROM last_cash),
 'end_available',(SELECT sum(available_after)::text FROM last_cash),
 'max_observed_drawdown',(SELECT max(e.drawdown)::text FROM equity_snapshots e JOIN scope s ON s.id=e.account_id WHERE e.created_at>=lo AND e.created_at<hi),
 'risk_events',(SELECT count(*) FROM risk_events e JOIN scope s ON s.id=e.account_id WHERE e.created_at>=lo AND e.created_at<hi),
 'observed_gaps',(SELECT count(*) FROM engine_events WHERE kind='GAP' AND received_at>=lo AND received_at<hi AND
      (aid IS NULL OR EXISTS (SELECT 1 FROM markets m JOIN paper_accounts a ON a.id=aid WHERE m.market_id=engine_events.market_id
          AND a.config->'assets' ? m.asset AND a.config->'timeframes' @> jsonb_build_array(m.timeframe)))),
 'probability_weighting','one earliest eligible observation per market','probability_samples',(SELECT count(*) FROM evidence),'brier',(SELECT avg((p-y)^2) FROM evidence),
 'log_loss',(SELECT -avg(y*ln(greatest(0.000000000000001,p))+(1-y)*ln(greatest(0.000000000000001,1-p))) FROM evidence),
 'ece',(SELECT sum(n*abs(predicted-frequency))/nullif(sum(n),0) FROM bins),
 'reliability',(SELECT coalesce(jsonb_agg(to_jsonb(bins) ORDER BY bin),'[]') FROM bins))
 INTO extra;
 -- An open position needs an observable mark at each period boundary. Carrying an expired mark into a report is not an equity observation.
 IF EXISTS(SELECT 1 FROM paper_positions p JOIN paper_accounts a ON p.account_id=a.id LEFT JOIN paper_settlements s USING(account_id,market_id)
    CROSS JOIN (VALUES(lo),(hi)) boundary(ts) WHERE a.mode='PAPER' AND (aid IS NULL OR a.id=aid) AND p.created_at<boundary.ts
    AND (s.created_at IS NULL OR s.created_at>=boundary.ts) AND NOT EXISTS(
        SELECT 1 FROM engine_events b WHERE b.kind='BOOK' AND b.market_id=p.market_id AND b.data->>'side'=p.side
        AND cardinality(b.quality_flags)=0 AND b.data->>'rule_version'=p.rule_version
        AND b.event_time>=boundary.ts-interval '3 seconds' AND b.received_at<boundary.ts
        AND b.data->>'rule_verified'='true' AND jsonb_array_length(b.data->'bids')>0)) THEN
   extra=extra||jsonb_build_object('closing_equity',NULL,'opening_equity',NULL,'net_pnl',NULL,'return',NULL,'valuation_quality','MISSING_BOUNDARY_MARK');
 ELSE
   extra=extra||jsonb_build_object('valuation_quality','RECORDED_ACCOUNT_MARKS');
 END IF;
 NEW.result=NEW.result||extra;
 RETURN NEW;
END $$;
