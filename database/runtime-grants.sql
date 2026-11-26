-- Reapplied after migrations by the owner. All names are project-owned roles.
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
GRANT USAGE ON SCHEMA public TO pq_api,pq_collector,pq_engine,pq_ops;
REVOKE ALL ON ALL TABLES IN SCHEMA public FROM pq_api,pq_collector,pq_engine,pq_ops;
REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM pq_api,pq_collector,pq_engine,pq_ops;

-- Explicit signatures prevent accidental grants on future overloads.
REVOKE EXECUTE ON ALL FUNCTIONS IN SCHEMA public FROM PUBLIC,pq_api,pq_collector,pq_engine,pq_ops;
ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE EXECUTE ON FUNCTIONS FROM PUBLIC;

-- Fixed-path domain commands own the writes needed for validated lifecycle,
-- report and approval transitions. API users cannot directly adjust balances.
ALTER FUNCTION pq_account_command(text,uuid,jsonb,text,text) SECURITY DEFINER SET search_path=pg_catalog,public,pg_temp;
ALTER FUNCTION pq_cancel(uuid,text,timestamptz) SECURITY DEFINER SET search_path=pg_catalog,public,pg_temp;
ALTER FUNCTION pq_report(uuid,timestamptz,timestamptz,text) SECURITY DEFINER SET search_path=pg_catalog,public,pg_temp;
ALTER FUNCTION pq_evaluate(jsonb,text) SECURITY DEFINER SET search_path=pg_catalog,public,pg_temp;
ALTER FUNCTION pq_promote(text,text,uuid,text,text) SECURITY DEFINER SET search_path=pg_catalog,public,pg_temp;
ALTER FUNCTION pq_source_transition() SECURITY DEFINER SET search_path=pg_catalog,public,pg_temp;
ALTER FUNCTION pq_research_notification() SECURITY DEFINER SET search_path=pg_catalog,public,pg_temp;

GRANT SELECT ON markets,engine_events,orderbook_snapshots,trade_ticks,reference_ticks,data_quality_events,
    dataset_versions,data_source_status,paper_accounts,paper_sessions,paper_orders,paper_fills,paper_positions,
    paper_settlements,balance_ledger,equity_snapshots,strategy_signals,strategy_evaluations,version_promotions,
    strategy_definitions,strategy_versions,comparison_experiments,research_experiments,backtest_runs,risk_events,
    trade_attributions,attribution_evidence,performance_reports,account_analytics,improvement_proposals,audit_logs,system_events,
    scheduled_jobs,system_settings,notification_logs,api_commands,user_sessions,market_resolutions,backtest_datasets TO pq_api;
GRANT SELECT(id,username,role),INSERT(username,password_hash,role) ON users TO pq_api;
GRANT INSERT,DELETE ON user_sessions TO pq_api;
GRANT INSERT ON backtest_runs,comparison_experiments,strategy_versions,improvement_proposals,api_commands,audit_logs TO pq_api;
GRANT INSERT,UPDATE ON backtest_datasets TO pq_api;
GRANT UPDATE(status,finished_at,dataset_id) ON backtest_runs TO pq_api;
GRANT UPDATE(status,reason,reviewed_at) ON improvement_proposals TO pq_api;
GRANT UPDATE(value,updated_at) ON system_settings TO pq_api;
GRANT UPDATE(next_run_at) ON scheduled_jobs TO pq_api;
-- FOR UPDATE/FOR SHARE in management commands require an UPDATE privilege.
GRANT UPDATE(name) ON paper_accounts TO pq_api;
GRANT EXECUTE ON FUNCTION pq_account_command(text,uuid,jsonb,text,text),pq_auth_salt(text),
    pq_auth_identity(text,text),pq_report(uuid,timestamptz,timestamptz,text),pq_evaluate(jsonb,text),
    pq_promote(text,text,uuid,text,text),pq_refresh_backtest_dataset(uuid) TO pq_api;

GRANT SELECT ON engine_events,markets,market_rule_versions,fee_schedules,market_resolutions,
    archived_raw_events,data_source_status TO pq_collector;
GRANT INSERT ON engine_events,markets,market_rule_versions,fee_schedules,market_resolutions,data_source_status TO pq_collector;
GRANT UPDATE ON markets,data_source_status TO pq_collector;

GRANT SELECT ON engine_events,paper_accounts,paper_sessions,paper_orders,paper_fills,paper_positions,
    paper_settlements,balance_ledger,equity_snapshots,strategy_signals,risk_events,trade_attributions,
    backtest_runs,backtest_datasets,markets,strategy_versions,system_settings,market_resolutions TO pq_engine;
GRANT INSERT ON paper_sessions,paper_orders,paper_fills,paper_positions,paper_settlements,balance_ledger,
    equity_snapshots,strategy_signals,risk_events,trade_attributions TO pq_engine;
GRANT UPDATE ON paper_accounts,paper_orders,paper_positions,backtest_runs TO pq_engine;
GRANT UPDATE(created_at) ON equity_snapshots,balance_ledger TO pq_engine;
GRANT EXECUTE ON FUNCTION pq_post(uuid,text,text,numeric,numeric,timestamptz,uuid,uuid),
    pq_cancel(uuid,text,timestamptz),pq_account_command(text,uuid,jsonb,text,text) TO pq_engine;

GRANT SELECT ON engine_events,markets,market_rule_versions,market_resolutions,fee_schedules,dataset_versions,
    archived_raw_events,scheduled_jobs,notification_logs,operation_cursors,risk_events,paper_accounts,
    paper_sessions,paper_orders,paper_positions,balance_ledger,system_events,system_settings,backtest_datasets TO pq_ops;
GRANT INSERT ON dataset_versions,operation_cursors,notification_logs,improvement_proposals,system_settings,
    risk_events,paper_sessions TO pq_ops;
GRANT UPDATE ON notification_logs,operation_cursors,scheduled_jobs,backtest_datasets TO pq_ops;
GRANT UPDATE(status) ON paper_accounts TO pq_ops;
GRANT UPDATE(value,updated_at) ON system_settings TO pq_ops;
GRANT SELECT(expires_at),DELETE ON user_sessions TO pq_ops;
GRANT EXECUTE ON FUNCTION pq_report(uuid,timestamptz,timestamptz,text),pq_cancel(uuid,text,timestamptz),
    pq_archive_raw(text),pq_refresh_backtest_dataset(uuid) TO pq_ops;
