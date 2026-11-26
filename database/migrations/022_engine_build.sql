-- Bind the first released implementation to its entire executable research build.
-- Preview accounts keep their frozen identity and retain their original version.
ALTER TABLE strategy_versions DISABLE TRIGGER pq_strategy_immutable;
DO $$
DECLARE sid text; digest text = 'afd072c9d198533dbc25903007de0c37d6cb6c50baf14689d5d321b75dcd16fb';
BEGIN
 FOREACH sid IN ARRAY ARRAY['S00','S01','S02','S03','S04'] LOOP
   IF EXISTS(SELECT 1 FROM paper_accounts WHERE strategy_id=sid OR
       coalesce(config->'strategies','[]') @> jsonb_build_array(jsonb_build_object('strategy_id',sid))) THEN
      INSERT INTO strategy_versions(strategy_id,version,implementation_hash) VALUES(sid,'1.0.1',digest);
   ELSE
      UPDATE strategy_versions SET implementation_hash=digest WHERE strategy_id=sid AND version='1.0.0';
   END IF;
 END LOOP;
END $$;
ALTER TABLE strategy_versions ENABLE TRIGGER pq_strategy_immutable;
