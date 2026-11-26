-- State changes enter the outbox through system_events, avoiding a notification per tick.
CREATE FUNCTION pq_source_transition() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF TG_OP='INSERT' OR OLD.status IS DISTINCT FROM NEW.status THEN
  INSERT INTO system_events(source,status,message,details)
  VALUES(NEW.source,NEW.status,'数据源 '||NEW.source||' → '||NEW.status,jsonb_build_object('last_success_at',NEW.last_success_at));
 END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER source_transition AFTER INSERT OR UPDATE OF status ON data_source_status
 FOR EACH ROW EXECUTE FUNCTION pq_source_transition();
CREATE FUNCTION pq_research_notification() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF TG_TABLE_NAME='backtest_runs' THEN
    IF NEW.status IN ('COMPLETED','FAILED','CANCELLED') AND OLD.status<>NEW.status THEN
      INSERT INTO system_events(source,status,message,details) VALUES('backtest',NEW.status,'回测 '||NEW.status,jsonb_build_object('run_id',NEW.id));
    END IF;
 ELSE
    INSERT INTO system_events(source,status,message,details) VALUES(TG_TABLE_NAME,NEW.status,'研究记录 '||NEW.status,jsonb_build_object('id',NEW.id));
 END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER backtest_notification AFTER UPDATE OF status ON backtest_runs FOR EACH ROW EXECUTE FUNCTION pq_research_notification();
CREATE TRIGGER proposal_notification AFTER INSERT ON improvement_proposals FOR EACH ROW EXECUTE FUNCTION pq_research_notification();
CREATE TRIGGER evaluation_notification AFTER INSERT ON strategy_evaluations FOR EACH ROW EXECUTE FUNCTION pq_research_notification();
