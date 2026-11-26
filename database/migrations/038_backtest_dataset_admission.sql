-- Dataset admission is a control-plane check. Keep historical economic
-- implementation hashes usable for both existing accounts and new replays.
CREATE FUNCTION pq_admit_backtest_dataset() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public,pg_temp AS $$
DECLARE d public.backtest_datasets;
BEGIN
    IF TG_OP='UPDATE' THEN
        IF NEW.dataset_id IS DISTINCT FROM OLD.dataset_id THEN
            RAISE EXCEPTION 'DATASET_BINDING_IMMUTABLE';
        END IF;
        IF OLD.status <> 'QUEUED' OR NEW.status NOT IN ('RUNNING','COMPLETED') THEN
            RETURN NEW;
        END IF;
    END IF;
    IF NEW.dataset_id IS NULL THEN
        RETURN NEW;
    END IF;

    SELECT * INTO STRICT d FROM public.backtest_datasets WHERE id=NEW.dataset_id FOR SHARE;
    IF d.expires_at <= clock_timestamp() THEN
        RAISE EXCEPTION 'DATA_UNAVAILABLE: REQUEST_EXPIRED';
    END IF;
    IF d.status <> 'BACKTEST_READY' THEN
        RAISE EXCEPTION '%', d.status;
    END IF;
    IF (NEW.config->>'start_time')::timestamptz IS DISTINCT FROM d.start_time
        OR (NEW.config->>'end_time')::timestamptz IS DISTINCT FROM d.end_time THEN
        RAISE EXCEPTION 'DATASET_PERIOD';
    END IF;
    RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION pq_admit_backtest_dataset() FROM PUBLIC;

CREATE TRIGGER backtest_dataset_admission
BEFORE INSERT OR UPDATE OF status,dataset_id ON backtest_runs
FOR EACH ROW EXECUTE FUNCTION pq_admit_backtest_dataset();
