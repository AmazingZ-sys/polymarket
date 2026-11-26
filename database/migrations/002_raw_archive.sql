-- Narrow lookup retained after a verified RAW payload moves to the Parquet volume.
ALTER TABLE dataset_versions ADD COLUMN raw_compacted_at timestamptz;
CREATE TABLE archived_raw_events (
    event_id text PRIMARY KEY,
    seq bigint UNIQUE NOT NULL,
    dataset_version text NOT NULL REFERENCES dataset_versions
);
CREATE INDEX engine_research_cursor ON engine_events(seq) WHERE kind IN ('BOOK','RESOLUTION','GAP');
