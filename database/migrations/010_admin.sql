CREATE TABLE users (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(), username text UNIQUE NOT NULL,
 password_hash text NOT NULL, role text NOT NULL CHECK(role IN ('ADMIN','VIEWER')),
 created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE user_sessions (
 token_hash text PRIMARY KEY, user_id uuid NOT NULL REFERENCES users, expires_at timestamptz NOT NULL
);
CREATE INDEX ON user_sessions(expires_at);
CREATE TABLE audit_logs (
 id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY, actor text NOT NULL, action text NOT NULL,
 resource text, details jsonb NOT NULL DEFAULT '{}', created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE api_commands (
 actor text NOT NULL, key text NOT NULL, fingerprint text NOT NULL, result jsonb NOT NULL,
 created_at timestamptz NOT NULL DEFAULT now(), PRIMARY KEY(actor,key)
);
CREATE TABLE system_events (
 id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY, source text NOT NULL, status text NOT NULL,
 message text NOT NULL, details jsonb NOT NULL DEFAULT '{}', created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE scheduled_jobs (
 name text PRIMARY KEY, next_run_at timestamptz NOT NULL DEFAULT now(), interval_seconds int NOT NULL CHECK(interval_seconds>0),
 last_run_at timestamptz, last_result text, failures int NOT NULL DEFAULT 0
);
CREATE TABLE performance_reports (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(), account_id uuid, start_time timestamptz NOT NULL,end_time timestamptz NOT NULL,
 period text NOT NULL, result jsonb NOT NULL, created_at timestamptz NOT NULL DEFAULT now(),
 UNIQUE NULLS NOT DISTINCT (account_id,start_time,end_time,period)
);
CREATE TABLE notification_logs (
 id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY, channel text NOT NULL, dedup_key text NOT NULL,
 message text NOT NULL,status text NOT NULL DEFAULT 'PENDING', attempts int NOT NULL DEFAULT 0,
 next_attempt_at timestamptz NOT NULL DEFAULT now(), created_at timestamptz NOT NULL DEFAULT now(), UNIQUE(channel,dedup_key)
);
CREATE INDEX ON notification_logs(next_attempt_at) WHERE status='PENDING';
CREATE TABLE improvement_proposals (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(), strategy_id text NOT NULL, problem text NOT NULL, evidence text NOT NULL,
 hypothesis text NOT NULL, acceptance text NOT NULL, status text NOT NULL DEFAULT 'PENDING', reason text,
 details jsonb NOT NULL DEFAULT '{}', created_at timestamptz NOT NULL DEFAULT now(), reviewed_at timestamptz
);
CREATE TABLE comparison_experiments (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(), name text NOT NULL, hypothesis text NOT NULL,
 account_ids uuid[] NOT NULL, criteria text NOT NULL, snapshot jsonb NOT NULL, created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE system_settings (name text PRIMARY KEY,value jsonb NOT NULL,updated_at timestamptz NOT NULL DEFAULT now());
INSERT INTO system_settings VALUES ('global_halt','false',now());
