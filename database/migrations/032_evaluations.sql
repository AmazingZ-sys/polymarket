CREATE TABLE strategy_evaluations (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(), experiment_id uuid NOT NULL REFERENCES comparison_experiments,
 account_id uuid NOT NULL REFERENCES paper_accounts, strategy_id text NOT NULL, strategy_version text NOT NULL,
 backtest_id uuid NOT NULL REFERENCES backtest_runs, stress_backtest_id uuid NOT NULL REFERENCES backtest_runs,
 status text NOT NULL CHECK(status IN ('INSUFFICIENT_DATA','VALIDATION_FAILED','VALIDATION_PASSED')),
 evidence text NOT NULL, metrics jsonb NOT NULL, checks jsonb NOT NULL,actor text NOT NULL,created_at timestamptz NOT NULL DEFAULT now());
CREATE UNIQUE INDEX one_champion ON strategy_versions(strategy_id) WHERE role='CHAMPION';
CREATE TABLE version_promotions (
 id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,strategy_id text NOT NULL,version text NOT NULL,
 evaluation_id uuid NOT NULL REFERENCES strategy_evaluations,actor text NOT NULL,reason text NOT NULL,
 previous_version text,created_at timestamptz NOT NULL DEFAULT now()
);
CREATE FUNCTION pq_evaluate(p jsonb, reviewer text) RETURNS jsonb LANGUAGE plpgsql AS $$
DECLARE a paper_accounts; exp comparison_experiments; base backtest_runs; stress backtest_runs; ba paper_accounts; sa paper_accounts;
 m jsonb; outcome text; r strategy_evaluations; samples bigint; days int; live_days int; eligible boolean; cost numeric;
BEGIN
 SELECT * INTO STRICT a FROM paper_accounts WHERE id=(p->>'account_id')::uuid AND mode='PAPER';
 SELECT * INTO STRICT exp FROM comparison_experiments WHERE id=(p->>'experiment_id')::uuid AND a.id=ANY(account_ids);
 SELECT * INTO STRICT base FROM backtest_runs WHERE id=(p->>'backtest_id')::uuid AND status='COMPLETED';
 SELECT * INTO STRICT stress FROM backtest_runs WHERE id=(p->>'stress_backtest_id')::uuid AND status='COMPLETED' AND id<>base.id;
 SELECT * INTO STRICT ba FROM paper_accounts WHERE id=base.account_id AND mode='BACKTEST';
 SELECT * INTO STRICT sa FROM paper_accounts WHERE id=stress.account_id AND mode='BACKTEST';
 IF (a.strategy_id,a.strategy_version,a.initial_balance) IS DISTINCT FROM (ba.strategy_id,ba.strategy_version,ba.initial_balance)
 OR (a.config - 'hypothesis' - 'cost_multiplier' - 'latency_multiplier') IS DISTINCT FROM (ba.config - 'hypothesis' - 'cost_multiplier' - 'latency_multiplier')
 OR (ba.config-'cost_multiplier') IS DISTINCT FROM (sa.config-'cost_multiplier') OR ba.strategy_id<>sa.strategy_id OR ba.strategy_version<>sa.strategy_version
 OR base.result->>'data_hash' IS DISTINCT FROM stress.result->>'data_hash'
 OR sa.initial_balance<>ba.initial_balance OR (stress.config->>'start_time')::timestamptz<>(base.config->>'start_time')::timestamptz
 OR (stress.config->>'end_time')::timestamptz<>(base.config->>'end_time')::timestamptz
 OR coalesce((sa.config->>'cost_multiplier')::numeric,1)<1.5*coalesce((ba.config->>'cost_multiplier')::numeric,1)
 THEN RAISE EXCEPTION 'base and cost-stress runs must be paired and match the candidate'; END IF;
 cost=(p->>'cost_usd')::numeric;
 SELECT count(*) INTO samples FROM paper_fills f JOIN paper_settlements s USING(account_id,market_id)
 WHERE f.account_id=ba.id AND f.created_at>=exp.created_at;
 SELECT count(DISTINCT (received_at AT TIME ZONE 'UTC')::date) INTO days FROM engine_events
 WHERE kind='BOOK' AND cardinality(quality_flags)=0 AND received_at BETWEEN (base.result->>'start_time')::timestamptz AND (base.result->>'end_time')::timestamptz;
 SELECT count(DISTINCT (created_at AT TIME ZONE 'UTC')::date) INTO live_days FROM strategy_signals WHERE account_id=a.id AND created_at>=exp.created_at;
 SELECT jsonb_build_object('out_of_sample_settled_fills',samples,'observed_utc_days',days,'paper_days',live_days,
 'paper_net_after_cost',(a.realized_pnl-cost)::text,'base_net_after_cost',((base.result->>'net_pnl')::numeric-cost)::text,
 'stress_net_after_cost',((stress.result->>'net_pnl')::numeric-cost)::text,
 'manual_operating_cost',cost::text,'profit_factor',profit_factor::text,'max_drawdown',a.max_drawdown::text,
 'bootstrap_ci',base.result->'mean_pnl_daily_block_ci','experiment_registered_at',exp.created_at,'protocol','initial-v1') INTO m FROM account_analytics WHERE account_id=a.id;
 eligible=days>=90 AND samples>=1000 AND live_days>=30 AND (base.result->>'start_time')::timestamptz>=exp.created_at
   AND jsonb_typeof(m->'bootstrap_ci')='array';
 outcome=CASE WHEN NOT coalesce(eligible,false) THEN 'INSUFFICIENT_DATA'
 WHEN (m->>'profit_factor')::numeric>1.3 AND a.max_drawdown<=0.05 AND (base.result->>'max_drawdown')::numeric<=0.05
 AND (m->>'paper_net_after_cost')::numeric>0 AND (m->>'base_net_after_cost')::numeric>0 AND (m->>'stress_net_after_cost')::numeric>0
 AND (m->'bootstrap_ci'->>0)::numeric>cost/greatest(samples,1)
 AND (p->'checks') @> '{"quality":true,"delay":true,"extreme":true,"multiple_testing":true,"independent_sample":true,"continuous_coverage":true}'
 THEN 'VALIDATION_PASSED' ELSE 'VALIDATION_FAILED' END;
 INSERT INTO strategy_evaluations(experiment_id,account_id,strategy_id,strategy_version,backtest_id,stress_backtest_id,status,evidence,metrics,checks,actor)
 VALUES(exp.id,a.id,a.strategy_id,a.strategy_version,base.id,stress.id,outcome,p->>'evidence',m,p->'checks',reviewer) RETURNING * INTO r;
 RETURN to_jsonb(r);
END $$;
CREATE FUNCTION pq_promote(sid text,ver text,eid uuid,reviewer text,why text) RETURNS jsonb LANGUAGE plpgsql AS $$
DECLARE ev strategy_evaluations; prev text; r version_promotions;
BEGIN
 PERFORM pg_advisory_xact_lock(hashtextextended(sid,24));
 SELECT * INTO STRICT ev FROM strategy_evaluations WHERE id=eid AND strategy_id=sid AND strategy_version=ver AND status='VALIDATION_PASSED';
 SELECT version INTO prev FROM strategy_versions WHERE strategy_id=sid AND role='CHAMPION';
 IF prev=ver THEN RAISE EXCEPTION 'version is already champion'; END IF;
 UPDATE strategy_versions SET role='RETIRED' WHERE strategy_id=sid AND role='CHAMPION';
 UPDATE strategy_versions SET role='CHAMPION' WHERE strategy_id=sid AND version=ver;
 INSERT INTO version_promotions(strategy_id,version,evaluation_id,actor,reason,previous_version) VALUES(sid,ver,eid,reviewer,why,prev) RETURNING * INTO r;
 RETURN to_jsonb(r);
END $$;
