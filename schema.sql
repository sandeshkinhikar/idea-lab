CREATE EXTENSION IF NOT EXISTS postgis;

CREATE TABLE IF NOT EXISTS wards (
  ward_id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  geom GEOMETRY(POLYGON, 4326) NOT NULL,
  provenance TEXT NOT NULL DEFAULT 'real_source'
);
CREATE TABLE IF NOT EXISTS edges (
  edge_id TEXT PRIMARY KEY,
  geom GEOMETRY(LINESTRING, 4326) NOT NULL,
  ward_id TEXT REFERENCES wards(ward_id),
  lst DOUBLE PRECISION, ndvi DOUBLE PRECISION, pm DOUBLE PRECISION,
  population DOUBLE PRECISION, slope DOUBLE PRECISION,
  ahp_suitability DOUBLE PRECISION, ahp_risk DOUBLE PRECISION,
  geometry_wkt TEXT, start_lat DOUBLE PRECISION, start_lon DOUBLE PRECISION,
  end_lat DOUBLE PRECISION, end_lon DOUBLE PRECISION,
  provenance TEXT NOT NULL DEFAULT 'real_source'
);
CREATE TABLE IF NOT EXISTS risk_scores (
  id BIGSERIAL PRIMARY KEY,
  ward_id TEXT REFERENCES wards(ward_id),
  edge_id TEXT REFERENCES edges(edge_id),
  ts TIMESTAMPTZ NOT NULL DEFAULT now(),
  ml_score DOUBLE PRECISION, ml_class TEXT,
  ahp_score DOUBLE PRECISION, ahp_class TEXT,
  contributions JSONB,
  provenance TEXT NOT NULL DEFAULT 'real_source'
);
CREATE TABLE IF NOT EXISTS model_runs (
  id BIGSERIAL PRIMARY KEY,
  algo TEXT NOT NULL,
  trained_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  params JSONB,
  accuracy DOUBLE PRECISION, precision DOUBLE PRECISION, recall DOUBLE PRECISION, f1 DOUBLE PRECISION,
  rmse DOUBLE PRECISION, mae DOUBLE PRECISION, r2 DOUBLE PRECISION,
  confusion_matrix JSONB,
  provenance TEXT NOT NULL DEFAULT 'real_source',
  details JSONB
);
CREATE TABLE IF NOT EXISTS alerts (
  id BIGSERIAL PRIMARY KEY,
  ward_id TEXT NOT NULL REFERENCES wards(ward_id),
  ts TIMESTAMPTZ NOT NULL DEFAULT now(),
  severity TEXT NOT NULL,
  cause TEXT NOT NULL,
  precaution TEXT NOT NULL,
  status TEXT NOT NULL CHECK(status IN ('open','ack','resolved')) DEFAULT 'open',
  provenance TEXT NOT NULL DEFAULT 'real_source'
);
CREATE TABLE IF NOT EXISTS sensor_readings (
  id BIGSERIAL PRIMARY KEY,
  ward_id TEXT NOT NULL REFERENCES wards(ward_id),
  device_id TEXT NOT NULL,
  ts TIMESTAMPTZ NOT NULL DEFAULT now(),
  temperature DOUBLE PRECISION,
  humidity DOUBLE PRECISION,
  quality TEXT NOT NULL,
  provenance TEXT NOT NULL DEFAULT 'real_source',
  event_key TEXT
);
CREATE TABLE IF NOT EXISTS pipeline_runs (
  id BIGSERIAL PRIMARY KEY,
  job TEXT NOT NULL,
  job_id TEXT,
  started_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  finished_at TIMESTAMPTZ,
  status TEXT NOT NULL,
  log TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS app_settings (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);

ALTER TABLE wards ADD COLUMN IF NOT EXISTS provenance TEXT NOT NULL DEFAULT 'real_source';
ALTER TABLE edges ADD COLUMN IF NOT EXISTS geometry_wkt TEXT;
ALTER TABLE edges ADD COLUMN IF NOT EXISTS start_lat DOUBLE PRECISION;
ALTER TABLE edges ADD COLUMN IF NOT EXISTS start_lon DOUBLE PRECISION;
ALTER TABLE edges ADD COLUMN IF NOT EXISTS end_lat DOUBLE PRECISION;
ALTER TABLE edges ADD COLUMN IF NOT EXISTS end_lon DOUBLE PRECISION;
ALTER TABLE edges ADD COLUMN IF NOT EXISTS provenance TEXT NOT NULL DEFAULT 'real_source';
ALTER TABLE risk_scores ADD COLUMN IF NOT EXISTS provenance TEXT NOT NULL DEFAULT 'real_source';
ALTER TABLE model_runs ADD COLUMN IF NOT EXISTS provenance TEXT NOT NULL DEFAULT 'real_source';
ALTER TABLE model_runs ADD COLUMN IF NOT EXISTS details JSONB;
ALTER TABLE alerts ADD COLUMN IF NOT EXISTS provenance TEXT NOT NULL DEFAULT 'real_source';
ALTER TABLE sensor_readings ADD COLUMN IF NOT EXISTS provenance TEXT NOT NULL DEFAULT 'real_source';
ALTER TABLE sensor_readings ADD COLUMN IF NOT EXISTS event_key TEXT;
ALTER TABLE pipeline_runs ADD COLUMN IF NOT EXISTS job_id TEXT;

CREATE INDEX IF NOT EXISTS wards_geom_gix ON wards USING GIST (geom);
CREATE INDEX IF NOT EXISTS edges_geom_gix ON edges USING GIST (geom);
CREATE INDEX IF NOT EXISTS edges_ward_idx ON edges (ward_id);
CREATE INDEX IF NOT EXISTS risk_scores_ward_ts_idx ON risk_scores (ward_id, ts DESC);
CREATE INDEX IF NOT EXISTS alerts_status_ts_idx ON alerts (status, ts DESC);
CREATE INDEX IF NOT EXISTS sensor_readings_ward_ts_idx ON sensor_readings (ward_id, ts DESC);
CREATE UNIQUE INDEX IF NOT EXISTS sensor_readings_event_key_uidx ON sensor_readings (event_key);
