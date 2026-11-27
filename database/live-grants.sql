GRANT USAGE ON SCHEMA public TO pq_live;
REVOKE ALL ON ALL TABLES IN SCHEMA public FROM pq_live;
REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM pq_live;
REVOKE ALL ON ALL FUNCTIONS IN SCHEMA public FROM pq_live;
GRANT SELECT ON polymarket_account_settings,polymarket_executor_health,polymarket_live_attempts,
    strategy_signals,paper_accounts,markets,system_settings TO pq_live;
GRANT INSERT,UPDATE ON polymarket_executor_health TO pq_live;
GRANT EXECUTE ON FUNCTION pq_live_reserve(bigint,bigint,numeric,numeric),pq_live_dispatch_ready(bigint,bigint),
    pq_live_result(bigint,text,text,text,numeric,numeric,numeric,numeric,text,text) TO pq_live;
GRANT EXECUTE ON FUNCTION pq_live_ack(bigint,text,text,boolean,boolean) TO pq_live;
