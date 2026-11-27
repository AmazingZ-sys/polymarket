-- Local authorization is isolated from simulated accounts and balances.
CREATE TABLE polymarket_account_settings (
    singleton boolean PRIMARY KEY CHECK (singleton),
    wallet_address text CHECK (wallet_address ~ '^0x[0-9A-Fa-f]{40}$' AND wallet_address <> '0x0000000000000000000000000000000000000000'),
    paper_account_id uuid REFERENCES paper_accounts,
    strategy_id text,
    strategy_version text,
    max_order_usd numeric(21,8) CHECK (max_order_usd > 0 AND max_order_usd < 'Infinity'::numeric),
    max_total_usd numeric(21,8) CHECK (max_total_usd >= max_order_usd AND max_total_usd < 'Infinity'::numeric),
    revision bigint NOT NULL DEFAULT 0,
    enabled boolean NOT NULL DEFAULT false,
    enabled_at timestamptz,
    enabled_after_signal bigint NOT NULL DEFAULT 0,
    attested_at timestamptz,
    attested_by text,
    CHECK (NOT enabled OR (wallet_address IS NOT NULL AND enabled_at IS NOT NULL AND attested_at IS NOT NULL AND attested_by IS NOT NULL)),
    geoblock_status text NOT NULL DEFAULT 'UNCHECKED' CHECK (geoblock_status IN ('UNCHECKED','ALLOWED','BLOCKED','UNKNOWN')),
    country text,
    region text,
    checked_at timestamptz,
    FOREIGN KEY(strategy_id,strategy_version) REFERENCES strategy_versions,
    CHECK (num_nonnulls(wallet_address,paper_account_id,strategy_id,strategy_version,max_order_usd,max_total_usd) IN (0,6))
);
INSERT INTO polymarket_account_settings(singleton) VALUES (true);

CREATE TABLE polymarket_executor_health (
    singleton boolean PRIMARY KEY CHECK (singleton),
    revision bigint NOT NULL,
    wallet_address text NOT NULL,
    signer_address text,
    auth_expires_at timestamptz,
    clob_only boolean NOT NULL,
    reconciled boolean NOT NULL,
    geoblock_allowed boolean NOT NULL,
    funds_ready boolean NOT NULL,
    healthy boolean NOT NULL,
    checked_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    detail_code text NOT NULL CHECK (detail_code ~ '^[A-Z0-9_]{1,64}$')
);
CREATE TABLE polymarket_live_attempts (
    signal_id bigint PRIMARY KEY REFERENCES strategy_signals,
    revision bigint NOT NULL,
    wallet_address text NOT NULL,
    up_amount numeric(21,8) NOT NULL,
    down_amount numeric(21,8) NOT NULL,
    state text NOT NULL CHECK (state IN ('RESERVED','CONFIRMED','REJECTED','UNKNOWN','EXPOSED')),
    up_order_id text, down_order_id text,
    up_principal numeric(21,8), down_principal numeric(21,8),
    up_fee numeric, down_fee numeric, up_fee_currency text, down_fee_currency text,
    CHECK (up_principal>=0 AND up_principal<'Infinity'::numeric AND down_principal>=0 AND down_principal<'Infinity'::numeric),
    CHECK (up_fee>=0 AND up_fee<'Infinity'::numeric AND down_fee>=0 AND down_fee<'Infinity'::numeric),
    CHECK (num_nonnulls(up_fee,up_fee_currency) IN (0,2) AND num_nonnulls(down_fee,down_fee_currency) IN (0,2)),
    CHECK (up_fee_currency ~ '^[a-zA-Z0-9:._-]{1,96}$' AND down_fee_currency ~ '^[a-zA-Z0-9:._-]{1,96}$'),
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    updated_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
CREATE INDEX polymarket_attempts_wallet ON polymarket_live_attempts(lower(wallet_address),state);

CREATE VIEW polymarket_execution_status AS
SELECT s.*, h.checked_at AS heartbeat_at, h.detail_code, h.auth_expires_at,
    coalesce(h.revision=s.revision AND lower(h.wallet_address)=lower(s.wallet_address)
        AND h.signer_address ~ '^0x[0-9A-Fa-f]{40}$' AND h.signer_address <> '0x0000000000000000000000000000000000000000'
        AND h.checked_at BETWEEN clock_timestamp()-interval '30 seconds' AND clock_timestamp()
        AND h.auth_expires_at>clock_timestamp() AND h.clob_only AND h.reconciled AND h.geoblock_allowed
        AND h.funds_ready AND h.healthy, false) AS executor_ready,
    coalesce(a.mode='PAPER' AND a.status='RUNNING',false) AS paper_ready,
    NOT EXISTS (SELECT FROM system_settings WHERE name='global_halt' AND value='false'::jsonb) AS halted,
    coalesce(sum(coalesce(l.up_principal,l.up_amount)+coalesce(l.down_principal,l.down_amount))
        FILTER (WHERE l.state<>'REJECTED'),0) AS committed_usd,
    coalesce(sum(coalesce(l.up_principal,0)+coalesce(l.down_principal,0)),0) AS confirmed_principal_usd,
    count(l.signal_id) FILTER (WHERE l.state<>'REJECTED' AND l.up_fee IS NULL)
      +count(l.signal_id) FILTER (WHERE l.state<>'REJECTED' AND l.down_fee IS NULL) AS pending_fee_legs,
    count(l.signal_id) FILTER (WHERE l.state IN ('RESERVED','EXPOSED','UNKNOWN')) AS unresolved
FROM polymarket_account_settings s LEFT JOIN polymarket_executor_health h ON h.singleton=s.singleton
    AND h.revision=s.revision AND lower(h.wallet_address)=lower(s.wallet_address)
LEFT JOIN paper_accounts a ON a.id=s.paper_account_id
LEFT JOIN polymarket_live_attempts l ON lower(l.wallet_address)=lower(s.wallet_address)
GROUP BY s.singleton,h.singleton,a.id;

CREATE FUNCTION pq_live_reserve(rev bigint, sid bigint, amount_up numeric, amount_down numeric)
RETURNS boolean LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public,pg_temp AS $$
DECLARE s public.polymarket_execution_status; inserted bigint;
BEGIN
    PERFORM singleton FROM public.polymarket_account_settings WHERE singleton FOR UPDATE;
    SELECT * INTO STRICT s FROM public.polymarket_execution_status;
    IF NOT s.enabled OR rev IS DISTINCT FROM s.revision OR NOT s.executor_ready OR NOT s.paper_ready OR s.halted
        OR s.geoblock_status<>'ALLOWED' OR s.unresolved>0 OR sid<=s.enabled_after_signal OR
        amount_up IS NULL OR amount_down IS NULL OR least(amount_up,amount_down)<=0 OR
        greatest(amount_up,amount_down)>s.max_order_usd OR amount_up<>round(amount_up,8) OR amount_down<>round(amount_down,8)
        OR s.committed_usd+amount_up+amount_down>s.max_total_usd THEN RETURN false; END IF;
    IF NOT EXISTS (SELECT FROM public.strategy_signals
        WHERE id=sid AND account_id=s.paper_account_id AND strategy_id=s.strategy_id AND strategy_version=s.strategy_version
          AND action='PAIR' AND created_at BETWEEN greatest(s.enabled_at,clock_timestamp()-interval '3 seconds')
          AND clock_timestamp()) THEN RETURN false; END IF;
    INSERT INTO public.polymarket_live_attempts(signal_id,revision,wallet_address,up_amount,down_amount,state)
        VALUES(sid,rev,s.wallet_address,amount_up,amount_down,'RESERVED')
        ON CONFLICT DO NOTHING RETURNING signal_id INTO inserted;
    RETURN inserted IS NOT NULL;
END $$;

-- Hold this transaction's shared setting lock until the single authorized request returns.
CREATE FUNCTION pq_live_dispatch_ready(rev bigint, sid bigint)
RETURNS boolean LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public,pg_temp AS $$
BEGIN
    PERFORM singleton FROM public.polymarket_account_settings WHERE singleton FOR SHARE;
    PERFORM name FROM public.system_settings WHERE name='global_halt' FOR SHARE;
    PERFORM a.id FROM public.paper_accounts a JOIN public.polymarket_account_settings s
        ON a.id=s.paper_account_id WHERE s.singleton FOR SHARE OF a;
    RETURN EXISTS (SELECT FROM public.polymarket_execution_status s JOIN public.polymarket_live_attempts l ON l.signal_id=sid
        JOIN public.strategy_signals signal ON signal.id=sid
        WHERE s.enabled AND s.revision=rev AND l.revision=rev AND l.state='RESERVED' AND s.unresolved=1
          AND lower(l.wallet_address)=lower(s.wallet_address) AND s.geoblock_status='ALLOWED' AND s.executor_ready
          AND s.paper_ready AND NOT s.halted AND signal.created_at>=clock_timestamp()-interval '3 seconds');
END $$;

CREATE FUNCTION pq_live_result(sid bigint, next_state text, up_id text, down_id text,
    up_spend numeric DEFAULT NULL, down_spend numeric DEFAULT NULL, up_cost numeric DEFAULT NULL, down_cost numeric DEFAULT NULL,
    up_currency text DEFAULT NULL, down_currency text DEFAULT NULL)
RETURNS void LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public,pg_temp AS $$
DECLARE a public.polymarket_live_attempts;
BEGIN
    PERFORM singleton FROM public.polymarket_account_settings WHERE singleton FOR UPDATE;
    SELECT * INTO STRICT a FROM public.polymarket_live_attempts WHERE signal_id=sid FOR UPDATE;
    IF next_state IS NULL OR next_state NOT IN ('CONFIRMED','REJECTED','UNKNOWN','EXPOSED') THEN
        RAISE EXCEPTION 'Invalid attempt result';
    END IF;
    IF (a.state IN ('CONFIRMED','REJECTED') AND a.state<>next_state)
        OR (a.state='EXPOSED' AND next_state='UNKNOWN')
        OR (next_state='REJECTED' AND greatest(a.up_principal,a.down_principal,up_spend,down_spend,a.up_fee,a.down_fee,up_cost,down_cost)>0)
        OR (a.up_order_id IS NOT NULL AND a.up_order_id<>up_id)
        OR (a.down_order_id IS NOT NULL AND a.down_order_id<>down_id)
        OR (a.up_principal IS NOT NULL AND a.up_principal<>up_spend)
        OR (a.down_principal IS NOT NULL AND a.down_principal<>down_spend)
        OR (a.up_fee IS NOT NULL AND a.up_fee<>up_cost)
        OR (a.down_fee IS NOT NULL AND a.down_fee<>down_cost)
        OR (a.up_fee_currency IS NOT NULL AND a.up_fee_currency<>up_currency)
        OR (a.down_fee_currency IS NOT NULL AND a.down_fee_currency<>down_currency)
        OR up_spend<>round(up_spend,8) OR down_spend<>round(down_spend,8) THEN
        RAISE EXCEPTION 'Conflicting execution evidence';
    END IF;
    UPDATE public.polymarket_live_attempts SET state=next_state,
        up_order_id=coalesce(a.up_order_id,up_id),down_order_id=coalesce(a.down_order_id,down_id),
        up_principal=coalesce(a.up_principal,up_spend),down_principal=coalesce(a.down_principal,down_spend),
        up_fee=coalesce(a.up_fee,up_cost),down_fee=coalesce(a.down_fee,down_cost),
        up_fee_currency=coalesce(a.up_fee_currency,up_currency),down_fee_currency=coalesce(a.down_fee_currency,down_currency),
        updated_at=clock_timestamp() WHERE signal_id=sid;
    IF next_state IN ('UNKNOWN','EXPOSED') THEN
        UPDATE public.polymarket_account_settings SET enabled=false
            WHERE singleton AND lower(wallet_address)=lower(a.wallet_address);
    END IF;
END $$;
-- The optional runtime role is granted these signatures by the owner only.
REVOKE ALL ON FUNCTION pq_live_reserve(bigint,bigint,numeric,numeric),pq_live_dispatch_ready(bigint,bigint),
    pq_live_result(bigint,text,text,text,numeric,numeric,numeric,numeric,text,text) FROM PUBLIC;

-- Persist the placement acknowledgement before inspecting asynchronous fills.
-- An absent ID without an explicit rejection remains an unknown outcome.
CREATE FUNCTION pq_live_ack(sid bigint, up_id text, down_id text, up_rejected boolean, down_rejected boolean)
RETURNS void LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public,pg_temp AS $$
DECLARE a public.polymarket_live_attempts;
BEGIN
    SELECT * INTO STRICT a FROM public.polymarket_live_attempts WHERE signal_id=sid FOR UPDATE;
    IF a.state<>'RESERVED' OR up_rejected IS NULL OR down_rejected IS NULL
        OR (up_id IS NOT NULL AND (up_id !~ '^0x[0-9a-fA-F]{64}$' OR up_rejected OR a.up_principal=0))
        OR (down_id IS NOT NULL AND (down_id !~ '^0x[0-9a-fA-F]{64}$' OR down_rejected OR a.down_principal=0))
        OR up_id=down_id
        OR (a.up_order_id IS NOT NULL AND (up_rejected OR a.up_order_id IS DISTINCT FROM up_id))
        OR (a.down_order_id IS NOT NULL AND (down_rejected OR a.down_order_id IS DISTINCT FROM down_id)) THEN
        RAISE EXCEPTION 'Conflicting placement acknowledgement';
    END IF;
    UPDATE public.polymarket_live_attempts SET
        up_order_id=coalesce(up_order_id,up_id),down_order_id=coalesce(down_order_id,down_id),
        up_principal=CASE WHEN up_rejected THEN 0 ELSE up_principal END,
        down_principal=CASE WHEN down_rejected THEN 0 ELSE down_principal END,
        up_fee=CASE WHEN up_rejected THEN 0 ELSE up_fee END,down_fee=CASE WHEN down_rejected THEN 0 ELSE down_fee END,
        up_fee_currency=CASE WHEN up_rejected THEN 'pUSD' ELSE up_fee_currency END,
        down_fee_currency=CASE WHEN down_rejected THEN 'pUSD' ELSE down_fee_currency END,
        updated_at=clock_timestamp() WHERE signal_id=sid;
END $$;
REVOKE ALL ON FUNCTION pq_live_ack(bigint,text,text,boolean,boolean) FROM PUBLIC;
