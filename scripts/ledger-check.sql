DO $$ BEGIN
 IF EXISTS(SELECT FROM paper_accounts a LEFT JOIN LATERAL
   (SELECT sum(available_delta) av,sum(frozen_delta) fr FROM balance_ledger WHERE account_id=a.id) l ON true
   WHERE a.available_balance IS DISTINCT FROM l.av OR a.frozen_balance IS DISTINCT FROM l.fr)
 THEN RAISE EXCEPTION 'ledger imbalance'; END IF;
 IF EXISTS(SELECT FROM (
   SELECT available_before,frozen_before,
          lag(available_after,1,0::numeric) OVER w previous_available,
          lag(frozen_after,1,0::numeric) OVER w previous_frozen
   FROM balance_ledger WINDOW w AS (PARTITION BY account_id ORDER BY id)
 ) entries WHERE available_before<>previous_available OR frozen_before<>previous_frozen)
 THEN RAISE EXCEPTION 'ledger discontinuity'; END IF;
END $$;
