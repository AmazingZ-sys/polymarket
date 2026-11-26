CREATE TABLE strategy_definitions(
 strategy_id text PRIMARY KEY, name text NOT NULL, description text NOT NULL,
 research_status text NOT NULL DEFAULT 'UNDER_RESEARCH', created_at timestamptz NOT NULL DEFAULT now()
);
INSERT INTO strategy_definitions(strategy_id,name,description) VALUES
 ('S00','No-trade baseline','Observe the market-implied UP probability and retain cash.'),
 ('S01','Complementary contracts','Independent, latency-adjusted IOC legs in one binary condition.'),
 ('S02','Settlement tail','Group observations by time to settlement and official input coverage.'),
 ('S03','Market probability','Score the observed UP midpoint against verified binary outcomes.'),
 ('S04','Cross-period observations','Compare simultaneous same-asset probability observations across horizons.');
CREATE TABLE strategy_versions(
 strategy_id text REFERENCES strategy_definitions, version text NOT NULL,
 parameters jsonb NOT NULL DEFAULT '{}', implementation_hash text NOT NULL,
 role text NOT NULL DEFAULT 'CHALLENGER' CHECK(role IN ('CHAMPION','CHALLENGER','RETIRED')),
 created_at timestamptz NOT NULL DEFAULT now(), PRIMARY KEY(strategy_id,version)
);
CREATE TABLE simulation_model_versions(
 version text PRIMARY KEY, latency_ms int NOT NULL, depth_fraction numeric NOT NULL, impact_ticks int NOT NULL,
 created_at timestamptz NOT NULL DEFAULT now()
);
INSERT INTO simulation_model_versions VALUES
 ('basic-v1',200,0.75,0),('conservative-v1',500,0.5,1),('stress-v1',1200,0.25,2);
CREATE TABLE paper_accounts(
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(), name text NOT NULL CHECK(length(trim(name)) BETWEEN 1 AND 120),
 mode text NOT NULL CHECK(mode IN ('PAPER','SHADOW','BACKTEST')),
 status text NOT NULL DEFAULT 'CREATED' CHECK(status IN
 ('CREATED','STARTING','RUNNING','PAUSED','RISK_HALTED','STOPPING','STOPPED','ERROR','ARCHIVED')),
 strategy_id text NOT NULL, strategy_version text NOT NULL, config jsonb NOT NULL, config_hash text NOT NULL,
 initial_balance numeric(28,8) NOT NULL CHECK(initial_balance > 0), available_balance numeric(28,8) NOT NULL DEFAULT 0 CHECK(available_balance>=0),
 frozen_balance numeric(28,8) NOT NULL DEFAULT 0 CHECK(frozen_balance>=0), realized_pnl numeric(28,8) NOT NULL DEFAULT 0,
 fees numeric(28,8) NOT NULL DEFAULT 0, equity numeric(28,8) NOT NULL, peak_equity numeric(28,8) NOT NULL,
 max_drawdown numeric NOT NULL DEFAULT 0, day_open numeric NOT NULL, equity_day date,
 last_seq bigint NOT NULL DEFAULT 0, fresh_after timestamptz, last_seen timestamptz,
 created_at timestamptz NOT NULL DEFAULT now(), started_at timestamptz, stopped_at timestamptz,
 FOREIGN KEY(strategy_id,strategy_version) REFERENCES strategy_versions
);
CREATE TABLE paper_sessions(
 id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY, account_id uuid NOT NULL REFERENCES paper_accounts,
 from_status text, status text NOT NULL, actor text NOT NULL, reason text NOT NULL DEFAULT '',
 created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE research_experiments(
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(), account_id uuid NOT NULL UNIQUE REFERENCES paper_accounts,
 name text NOT NULL, hypothesis text NOT NULL, config jsonb NOT NULL, config_hash text NOT NULL,
 created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE strategy_signals(
 id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,account_id uuid NOT NULL REFERENCES paper_accounts,market_id text NOT NULL,
 event_id text NOT NULL, strategy_id text NOT NULL,strategy_version text NOT NULL,config_hash text NOT NULL,
 action text NOT NULL, reason text NOT NULL, evidence jsonb NOT NULL, created_at timestamptz NOT NULL, UNIQUE(account_id,event_id,strategy_id)
);
CREATE TABLE paper_orders(
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(),account_id uuid NOT NULL REFERENCES paper_accounts,signal_id bigint NOT NULL REFERENCES strategy_signals,
 market_id text NOT NULL,asset text NOT NULL,side text NOT NULL CHECK(side IN ('UP','DOWN')),status text NOT NULL,
 CHECK(status IN ('PENDING','OPEN','PARTIALLY_FILLED','FILLED','CANCELLED','EXPIRED','REJECTED')),
 quantity numeric NOT NULL CHECK(quantity>0),limit_price numeric NOT NULL CHECK(limit_price>0 AND limit_price<=1),
 reserved numeric(28,8) NOT NULL CHECK(reserved>=0),filled_quantity numeric NOT NULL DEFAULT 0,reason text,
 rule_version text NOT NULL,created_at timestamptz NOT NULL,arrival_time timestamptz NOT NULL,expires_at timestamptz NOT NULL,
 UNIQUE(account_id,market_id,side)
);
CREATE TABLE paper_fills(
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(),account_id uuid NOT NULL REFERENCES paper_accounts,order_id uuid NOT NULL UNIQUE REFERENCES paper_orders,
 market_id text NOT NULL,side text NOT NULL,quantity numeric NOT NULL,price numeric NOT NULL,notional numeric(28,8) NOT NULL,
 fee numeric(28,8) NOT NULL,slippage numeric(28,8) NOT NULL,event_id text NOT NULL,evidence jsonb NOT NULL,created_at timestamptz NOT NULL
);
CREATE TABLE paper_positions(
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(),account_id uuid NOT NULL REFERENCES paper_accounts,market_id text NOT NULL,asset text NOT NULL,
 side text NOT NULL,quantity numeric NOT NULL,cost numeric(28,8) NOT NULL,rule_version text NOT NULL,
 mark_price numeric,mark_time timestamptz,status text NOT NULL DEFAULT 'OPEN',
 created_at timestamptz NOT NULL,UNIQUE(account_id,market_id,side)
);
CREATE TABLE paper_settlements(
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(),account_id uuid NOT NULL REFERENCES paper_accounts, market_id text NOT NULL,
 outcome text NOT NULL,payout numeric(28,8) NOT NULL,cost numeric(28,8) NOT NULL,realized_pnl numeric(28,8) NOT NULL,
 source text NOT NULL,rule_version text NOT NULL,event_id text NOT NULL,created_at timestamptz NOT NULL,UNIQUE(account_id,market_id)
);
CREATE TABLE balance_ledger(
 id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,account_id uuid NOT NULL REFERENCES paper_accounts,
 order_id uuid REFERENCES paper_orders,fill_id uuid REFERENCES paper_fills,reason text NOT NULL,idempotency_key text NOT NULL,
 available_before numeric(28,8) NOT NULL,available_delta numeric(28,8) NOT NULL,available_after numeric(28,8) NOT NULL,
 frozen_before numeric(28,8) NOT NULL,frozen_delta numeric(28,8) NOT NULL,frozen_after numeric(28,8) NOT NULL,
 created_at timestamptz NOT NULL,UNIQUE(account_id,idempotency_key),
 CHECK(available_before+available_delta=available_after AND frozen_before+frozen_delta=frozen_after)
);
CREATE TABLE equity_snapshots(
 id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,account_id uuid NOT NULL REFERENCES paper_accounts,
 equity numeric NOT NULL,drawdown numeric NOT NULL,quality text NOT NULL,created_at timestamptz NOT NULL
);
CREATE TABLE risk_events(
 id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,account_id uuid NOT NULL REFERENCES paper_accounts,
 market_id text,reason text NOT NULL,evidence jsonb NOT NULL,created_at timestamptz NOT NULL
);
CREATE TABLE trade_attributions(
 id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,account_id uuid NOT NULL REFERENCES paper_accounts,
 fill_id uuid NOT NULL UNIQUE REFERENCES paper_fills,market_id text NOT NULL,reason text NOT NULL,
 net_pnl numeric NOT NULL,evidence jsonb NOT NULL,created_at timestamptz NOT NULL
);
CREATE TABLE backtest_runs(
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(),config jsonb NOT NULL,status text NOT NULL DEFAULT 'QUEUED'
 CHECK(status IN ('QUEUED','RUNNING','CANCELLED','COMPLETED','FAILED')),
 progress numeric NOT NULL DEFAULT 0,result jsonb,account_id uuid UNIQUE REFERENCES paper_accounts,
 boundary_seq bigint,first_seq bigint,created_at timestamptz NOT NULL DEFAULT now(),finished_at timestamptz
);
CREATE TABLE engine_commands(
 actor text NOT NULL,key text NOT NULL,request jsonb NOT NULL,result jsonb NOT NULL,created_at timestamptz DEFAULT now(),PRIMARY KEY(actor,key)
);
CREATE INDEX paper_sessions_history ON paper_sessions(account_id,created_at DESC);
CREATE INDEX ledger_history ON balance_ledger(account_id,id);
CREATE INDEX outstanding_orders ON paper_orders(account_id,arrival_time) WHERE status IN ('PENDING','OPEN');
CREATE INDEX signal_timeline ON strategy_signals(account_id,market_id,created_at);
CREATE INDEX equity_history ON equity_snapshots(account_id,created_at DESC);
CREATE INDEX open_positions ON paper_positions(account_id) WHERE status='OPEN';
CREATE INDEX pending_backtests ON backtest_runs(created_at) WHERE status IN ('QUEUED','RUNNING');

CREATE FUNCTION pq_post(a uuid,k text,why text,av numeric,fr numeric,ts timestamptz,ord uuid DEFAULT NULL,fill uuid DEFAULT NULL)
RETURNS void LANGUAGE plpgsql AS $$
DECLARE old paper_accounts;
BEGIN
 SELECT * INTO STRICT old FROM paper_accounts WHERE id=a FOR UPDATE;
 INSERT INTO balance_ledger(account_id,order_id,fill_id,reason,idempotency_key,available_before,available_delta,available_after,
 frozen_before,frozen_delta,frozen_after,created_at)
 VALUES(a,ord,fill,why,k,old.available_balance,av,old.available_balance+av,old.frozen_balance,fr,old.frozen_balance+fr,ts);
 UPDATE paper_accounts SET available_balance=available_balance+av,frozen_balance=frozen_balance+fr WHERE id=a;
END $$;
CREATE FUNCTION pq_cancel(a uuid,why text,ts timestamptz) RETURNS void LANGUAGE plpgsql AS $$
DECLARE o paper_orders;
BEGIN
 FOR o IN SELECT * FROM paper_orders WHERE account_id=a AND status IN ('PENDING','OPEN') FOR UPDATE LOOP
  PERFORM pq_post(a,'cancel/'||o.id,'RELEASE',o.reserved,-o.reserved,ts,o.id);
  UPDATE paper_orders SET status='CANCELLED',reserved=0,reason=why WHERE id=o.id;
 END LOOP;
END $$;
CREATE FUNCTION pq_account_json(a uuid) RETURNS jsonb LANGUAGE sql AS $$
 SELECT to_jsonb(p) || jsonb_build_object('initial_balance',initial_balance::text,'available_balance',available_balance::text,
 'frozen_balance',frozen_balance::text,'realized_pnl',realized_pnl::text,'equity',equity::text,'peak_equity',peak_equity::text,
 'fees',fees::text,'max_drawdown',max_drawdown::text,'day_open',day_open::text) FROM paper_accounts p WHERE id=a
$$;
CREATE FUNCTION pq_account_command(act text,a uuid,p jsonb,k text,who_name text) RETURNS jsonb LANGUAGE plpgsql AS $$
DECLARE old paper_accounts; c engine_commands; req jsonb; res jsonb; cfg jsonb; init numeric; dest text; m text; sid text;
 ver text; nm text; idout uuid; v strategy_versions; model simulation_model_versions;
BEGIN
 IF k IS NULL OR length(k) NOT BETWEEN 1 AND 200 OR who_name IS NULL THEN RAISE EXCEPTION 'invalid command key or actor'; END IF;
 req=jsonb_build_object('action',act,'id',a,'payload',p);
 PERFORM pg_advisory_xact_lock(hashtextextended(who_name||'/'||k,0));
 SELECT * INTO c FROM engine_commands WHERE actor=who_name AND key=k;
 IF FOUND THEN
  IF c.request<>req THEN RAISE EXCEPTION 'idempotency conflict'; END IF;
  RETURN c.result;
 END IF;
 IF act<>'create' THEN SELECT * INTO STRICT old FROM paper_accounts WHERE id=a FOR UPDATE; END IF;
 IF act IN ('create','clone') THEN
  IF act='clone' THEN p=jsonb_build_object('name',coalesce(p->>'name',left(old.name,110)||' (clone)'),
    'mode',old.mode,'initial_balance',old.initial_balance,'strategy_id',old.strategy_id,'strategy_version',old.strategy_version,'config',old.config); END IF;
  init=coalesce(p->>'initial_balance','1000')::numeric; m=coalesce(p->>'mode','PAPER');
  IF init<>round(init,8) OR init NOT BETWEEN 0.00000001 AND 1000000000000 THEN RAISE EXCEPTION 'invalid initial balance'; END IF;
  sid=coalesce(p->>'strategy_id','S00'); ver=coalesce(p->>'strategy_version','1.0.0'); nm=trim(p->>'name');
  SELECT * INTO STRICT v FROM strategy_versions WHERE strategy_id=sid AND version=ver;
  cfg='{"assets":["BTC","ETH"],"timeframes":[5,15],"simulation_model":"conservative-v1","tail_seconds":60,"risk_per_trade":"0.005","max_drawdown":"0.05","daily_loss":"0.02","market_cap":"0.02","asset_cap":"0.1","portfolio_cap":"0.2"}'::jsonb
    || v.parameters || coalesce(p->'config','{}') || jsonb_build_object('implementation_hash',v.implementation_hash);
  SELECT * INTO STRICT model FROM simulation_model_versions WHERE version=cfg->>'simulation_model';
  cfg=cfg || jsonb_build_object('latency_ms',model.latency_ms,'depth_fraction',model.depth_fraction::text,'impact_ticks',model.impact_ticks);
  IF (cfg->>'risk_per_trade')::numeric NOT BETWEEN 0.000001 AND 0.005 OR (cfg->>'max_drawdown')::numeric NOT BETWEEN 0.000001 AND 0.05 OR
    (cfg->>'daily_loss')::numeric NOT BETWEEN 0.000001 AND 0.02 OR (cfg->>'market_cap')::numeric NOT BETWEEN 0.000001 AND 1 OR
    (cfg->>'asset_cap')::numeric NOT BETWEEN 0.000001 AND 1 OR (cfg->>'portfolio_cap')::numeric NOT BETWEEN 0.000001 AND 1 OR
    NOT (cfg->'assets' <@ '["BTC","ETH"]'::jsonb) OR NOT (cfg->'timeframes' <@ '[5,15]'::jsonb)
    THEN RAISE EXCEPTION 'invalid research configuration'; END IF;
  INSERT INTO paper_accounts(name,mode,initial_balance,equity,peak_equity,day_open,strategy_id,strategy_version,config,config_hash)
   VALUES(nm,m,init,init,init,init,sid,ver,cfg,encode(sha256(convert_to(cfg::text,'UTF8')),'hex')) RETURNING id INTO idout;
  PERFORM pq_post(idout,'initial','INITIAL',init,0,now());
  INSERT INTO research_experiments(account_id,name,hypothesis,config,config_hash) SELECT id,name,
   coalesce(config->>'hypothesis','Measure net performance against the no-trade baseline.'),config,config_hash FROM paper_accounts WHERE id=idout;
  INSERT INTO paper_sessions(account_id,status,actor) VALUES(idout,'CREATED',who_name);
  INSERT INTO equity_snapshots(account_id,equity,drawdown,quality,created_at) VALUES(idout,init,0,'CASH',now());
 ELSE
  idout=a;
  dest=CASE WHEN act='start' AND old.status='CREATED' THEN 'STARTING'
    WHEN act='resume' AND old.status IN ('PAUSED','RISK_HALTED','ERROR') THEN 'STARTING'
    WHEN act='pause' AND old.status IN ('STARTING','RUNNING') THEN 'PAUSED'
    WHEN act='stop' AND old.status IN ('CREATED','STARTING','RUNNING','PAUSED','RISK_HALTED','ERROR') THEN 'STOPPING'
    WHEN act='archive' AND old.status='STOPPED' AND NOT EXISTS(SELECT FROM paper_positions WHERE account_id=a AND status='OPEN') THEN 'ARCHIVED'
    ELSE NULL END;
  IF dest IS NULL THEN RAISE EXCEPTION 'invalid state transition'; END IF;
  PERFORM pq_cancel(a,act,now());
  INSERT INTO paper_sessions(account_id,from_status,status,actor) VALUES(a,old.status,dest,who_name);
  IF dest='STOPPING' THEN INSERT INTO paper_sessions(account_id,from_status,status,actor) VALUES(a,dest,'STOPPED',who_name);dest='STOPPED'; END IF;
  UPDATE paper_accounts SET status=dest,stopped_at=CASE WHEN dest='STOPPED' THEN now() ELSE stopped_at END,
   fresh_after=CASE WHEN dest='STARTING' THEN now() ELSE fresh_after END WHERE id=a;
 END IF;
 res=pq_account_json(idout);
 INSERT INTO engine_commands(actor,key,request,result) VALUES(who_name,k,req,res);
 RETURN res;
END $$;
INSERT INTO strategy_versions(strategy_id,version,implementation_hash) SELECT strategy_id,'1.0.0','d060cf801c1c99348338ba40a3958bcd6f7f916360c550d149e85399975f1295' FROM strategy_definitions;
