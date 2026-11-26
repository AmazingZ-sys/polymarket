CREATE FUNCTION pq_report(a uuid,lo timestamptz,hi timestamptz,period_name text) RETURNS jsonb LANGUAGE plpgsql AS $$
DECLARE result jsonb; output performance_reports;
BEGIN
 IF hi<=lo OR hi>now()+interval '1 second' THEN RAISE EXCEPTION 'invalid report period'; END IF;
 IF a IS NOT NULL AND NOT EXISTS(SELECT FROM paper_accounts WHERE id=a AND mode='PAPER') THEN RAISE EXCEPTION 'paper account required'; END IF;
 WITH scope AS (SELECT * FROM paper_accounts WHERE mode='PAPER' AND (a IS NULL OR id=a) AND created_at<hi),
 boundaries AS (SELECT s.id,s.initial_balance,coalesce(first.equity,s.initial_balance) opening,last.equity closing,last.quality,last.created_at last_mark
 FROM scope s LEFT JOIN LATERAL(SELECT equity FROM equity_snapshots e WHERE e.account_id=s.id AND e.created_at<=lo ORDER BY created_at DESC,id DESC LIMIT 1) first ON true
 LEFT JOIN LATERAL(SELECT equity,quality,created_at FROM equity_snapshots e WHERE e.account_id=s.id AND e.created_at<hi ORDER BY created_at DESC,id DESC LIMIT 1) last ON true),
 cash AS (SELECT count(*) accounts,sum(initial_balance) initial,sum(opening) opening,
 CASE WHEN count(*)=count(closing) THEN sum(closing) END closing,min(last_mark) oldest_mark FROM boundaries),
 settled AS (SELECT p.* FROM paper_settlements p JOIN scope s ON s.id=p.account_id WHERE p.created_at>=lo AND p.created_at<hi),
 pnl AS (SELECT count(*) n, sum(realized_pnl) realized,avg(realized_pnl) expectancy,avg((realized_pnl>0)::int) win_rate,
 avg(realized_pnl) FILTER(WHERE realized_pnl>0) average_win,avg(realized_pnl) FILTER(WHERE realized_pnl<0) average_loss,
 min(realized_pnl) worst, sum(greatest(realized_pnl,0)) gains,sum(greatest(-realized_pnl,0)) losses FROM settled),
 trades AS (SELECT count(*) n,sum(f.fee) fees,sum(f.slippage) slippage FROM paper_fills f JOIN scope s ON s.id=f.account_id WHERE f.created_at>=lo AND f.created_at<hi),
 orders AS (SELECT count(*) FILTER(WHERE o.status IN ('EXPIRED','CANCELLED','REJECTED') AND o.filled_quantity=0) unfilled,count(*) FILTER(WHERE o.filled_quantity>0 AND o.filled_quantity<o.quantity) partial FROM paper_orders o JOIN scope s ON s.id=o.account_id WHERE o.created_at>=lo AND o.created_at<hi)
 SELECT jsonb_build_object('accounts',cash.accounts,'initial_balance',cash.initial::text,'opening_equity',cash.opening::text,'closing_equity',cash.closing::text,
 'oldest_closing_mark',cash.oldest_mark,'net_pnl',(cash.closing-cash.opening)::text,'return',((cash.closing-cash.opening)/nullif(cash.opening,0))::text,
 'realized_pnl',pnl.realized::text,'settlements',pnl.n,'win_rate',pnl.win_rate::text,'expectancy',pnl.expectancy::text,
 'average_win',pnl.average_win::text,'average_loss',pnl.average_loss::text,'worst_settlement',pnl.worst::text,
 'profit_factor',(pnl.gains/nullif(pnl.losses,0))::text,'profit_factor_status',CASE WHEN pnl.n=0 THEN 'NO_SAMPLES' WHEN pnl.losses=0 THEN 'NO_LOSSES' ELSE 'DEFINED' END,
 'fills',trades.n,'fees',trades.fees::text,'slippage',trades.slippage::text,'unfilled',orders.unfilled,'partial',orders.partial,
 'infrastructure_cost',NULL,'project_net_pnl',NULL,'confidence_interval',NULL,'validation','INSUFFICIENT_DATA')
 INTO result FROM cash,pnl,trades,orders;
 INSERT INTO performance_reports(account_id,start_time,end_time,period,result) VALUES(a,lo,hi,period_name,result)
 ON CONFLICT (account_id,start_time,end_time,period) DO NOTHING;
 SELECT * INTO STRICT output FROM performance_reports WHERE account_id IS NOT DISTINCT FROM a AND start_time=lo AND end_time=hi AND period=period_name;
 RETURN to_jsonb(output);
END $$;
