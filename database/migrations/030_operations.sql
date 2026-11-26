CREATE VIEW account_analytics AS
 SELECT a.id account_id,a.name,a.mode,a.strategy_id,a.strategy_version,a.status,a.initial_balance,
 a.equity,a.realized_pnl,a.fees,a.equity-a.initial_balance net_pnl,a.max_drawdown,
 f.fills,f.slippage,s.settlements,s.win_rate,s.profit_factor,a.created_at
 FROM paper_accounts a
 CROSS JOIN LATERAL (SELECT count(*) fills,sum(slippage) slippage FROM paper_fills WHERE account_id=a.id) f
 CROSS JOIN LATERAL (SELECT count(*) settlements,
 avg((realized_pnl>0)::int) win_rate,sum(greatest(realized_pnl,0))/nullif(sum(greatest(-realized_pnl,0)),0) profit_factor
 FROM paper_settlements WHERE account_id=a.id) s;
CREATE INDEX fills_account_time ON paper_fills(account_id,created_at DESC);
CREATE INDEX pnl_account_time ON paper_settlements(account_id,created_at DESC);
CREATE TABLE operation_cursors (name text PRIMARY KEY, last_id bigint NOT NULL);
INSERT INTO scheduled_jobs(name,interval_seconds) VALUES
 ('ledger_check',60),('system_health',60),('session_cleanup',3600),('daily_report',3600),('weekly_report',3600),
 ('monthly_report',3600),('event_notifications',30),('archive',600);
