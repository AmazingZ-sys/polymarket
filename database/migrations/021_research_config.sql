-- Enforce durable parameter versions and event replay indexes.
CREATE INDEX engine_replay_window ON engine_events(seq) WHERE kind IN ('BOOK','GAP','RESOLUTION');
ALTER TABLE paper_accounts ADD COLUMN last_book_at timestamptz;
ALTER TABLE paper_accounts ADD COLUMN consecutive_losses integer NOT NULL DEFAULT 0;
CREATE FUNCTION pq_valid_config(c jsonb) RETURNS void LANGUAGE plpgsql AS $$
DECLARE key_name text;
BEGIN
 IF jsonb_typeof(c)<>'object' THEN RAISE EXCEPTION 'invalid config'; END IF;
 FOREACH key_name IN ARRAY ARRAY['risk_per_trade','daily_loss','max_drawdown','market_cap','asset_cap','portfolio_cap','tail_seconds'] LOOP
  IF coalesce(c->>key_name,'') !~ '^[0-9]+(\.[0-9]+)?$' THEN RAISE EXCEPTION 'invalid config field %',key_name; END IF;
 END LOOP;
 IF (c->>'tail_seconds')::int NOT BETWEEN 1 AND 900 OR jsonb_typeof(c->'assets')<>'array'
 OR jsonb_typeof(c->'timeframes')<>'array' OR jsonb_array_length(c->'assets')<1 OR jsonb_array_length(c->'timeframes')<1
 OR coalesce(c->>'minimum_edge','0')::numeric NOT BETWEEN 0 AND 1 OR coalesce(c->>'cost_multiplier','1')::numeric NOT BETWEEN 1 AND 3
 OR coalesce(c->>'latency_multiplier','1')::numeric NOT BETWEEN 1 AND 5 THEN RAISE EXCEPTION 'invalid experiment assumptions'; END IF;
END $$;
CREATE FUNCTION pq_freeze_config() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE v strategy_versions; member jsonb; members jsonb='[]'; seen text[]=ARRAY[NEW.strategy_id];
BEGIN
 IF TG_OP='UPDATE' THEN
   IF (OLD.strategy_id,OLD.strategy_version,OLD.config,OLD.initial_balance,OLD.mode)
    IS DISTINCT FROM (NEW.strategy_id,NEW.strategy_version,NEW.config,NEW.initial_balance,NEW.mode)
    THEN RAISE EXCEPTION 'account configuration is immutable'; END IF;
   RETURN NEW;
 END IF;
 PERFORM pq_valid_config(NEW.config);
 IF NEW.config ? 'strategies' THEN
   IF jsonb_typeof(NEW.config->'strategies')<>'array' OR jsonb_array_length(NEW.config->'strategies')>4
    THEN RAISE EXCEPTION 'invalid additional strategies'; END IF;
   FOR member IN SELECT value FROM jsonb_array_elements(NEW.config->'strategies') LOOP
     SELECT * INTO STRICT v FROM strategy_versions WHERE strategy_id=member->>'strategy_id' AND version=member->>'strategy_version';
     IF v.strategy_id=ANY(seen) THEN RAISE EXCEPTION 'duplicate strategy'; END IF;
     seen=array_append(seen,v.strategy_id);
     members=members||jsonb_build_array(jsonb_build_object('strategy_id',v.strategy_id,'strategy_version',v.version,
        'parameters',v.parameters,'implementation_hash',v.implementation_hash));
   END LOOP;
   NEW.config=jsonb_set(NEW.config,'{strategies}',members);
 END IF;
 NEW.config_hash=encode(sha256(convert_to(NEW.config::text,'UTF8')),'hex');
 RETURN NEW;
END $$;
CREATE TRIGGER pq_frozen_params BEFORE INSERT OR UPDATE OF config,strategy_id,strategy_version,mode,initial_balance
 ON paper_accounts FOR EACH ROW EXECUTE FUNCTION pq_freeze_config();
CREATE FUNCTION pq_fixed_strategy() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF (OLD.strategy_id,OLD.version,OLD.parameters,OLD.implementation_hash) IS DISTINCT FROM
 (NEW.strategy_id,NEW.version,NEW.parameters,NEW.implementation_hash) THEN RAISE EXCEPTION 'strategy version is immutable'; END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER pq_strategy_immutable BEFORE UPDATE ON strategy_versions FOR EACH ROW EXECUTE FUNCTION pq_fixed_strategy();
