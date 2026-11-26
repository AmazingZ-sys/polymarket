CREATE FUNCTION pq_auth_salt(login_name text) RETURNS text
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,public,pg_temp AS $$
  SELECT split_part(password_hash,'.',1) FROM public.users WHERE username=login_name
$$;
CREATE FUNCTION pq_auth_identity(login_name text, candidate text)
RETURNS TABLE(id uuid,username text,role text)
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,public,pg_temp AS $$
  SELECT u.id,u.username,u.role FROM public.users u WHERE u.username=login_name
  AND sha256(convert_to(split_part(u.password_hash,'.',2),'UTF8'))=sha256(convert_to(candidate,'UTF8'))
$$;
REVOKE ALL ON FUNCTION pq_auth_salt(text), pq_auth_identity(text,text) FROM PUBLIC;

-- Only verified bundles older than the hot retention period are eligible.
CREATE FUNCTION pq_archive_raw(batch text) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public,pg_temp AS $$
DECLARE d public.dataset_versions;
BEGIN
 SELECT * INTO STRICT d FROM public.dataset_versions WHERE version=batch AND raw_compacted_at IS NULL
     AND created_at < now()-interval '1 hour' FOR UPDATE;
 INSERT INTO public.archived_raw_events SELECT event_id,seq,batch FROM public.engine_events
     WHERE seq BETWEEN d.first_seq AND d.last_seq AND kind='RAW';
 DELETE FROM public.engine_events WHERE seq BETWEEN d.first_seq AND d.last_seq AND kind='RAW';
 UPDATE public.dataset_versions SET raw_compacted_at=now() WHERE version=batch;
END $$;
REVOKE ALL ON FUNCTION pq_archive_raw(text) FROM PUBLIC;
