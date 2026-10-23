"""Dual SQLite demo / PostgreSQL + PostGIS persistence for GeoCool."""
from __future__ import annotations

import json
import os
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import unquote, urlparse

ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data"
DEFAULT_DB = DATA_DIR / "geocool_demo.sqlite"
_ID_TABLES = {"alerts", "sensor_readings", "risk_scores", "model_runs", "pipeline_runs"}


def _database_url() -> str:
    value = os.getenv("DATABASE_URL", "").strip()
    if value.startswith("postgres://"):
        value = "postgresql://" + value[len("postgres://"):]
    if value.startswith("postgresql+psycopg://"):
        value = "postgresql://" + value[len("postgresql+psycopg://"):]
    return value


def is_postgres() -> bool:
    return _database_url().startswith(("postgres://", "postgresql://"))


def _database_path() -> Path:
    value = _database_url()
    if not value:
        return DEFAULT_DB
    if value.startswith("sqlite:"):
        parsed = urlparse(value)
        if parsed.path in ("", "/:memory:"):
            return Path(":memory:")
        if parsed.netloc and parsed.netloc not in ("", "localhost"):
            return Path("/") / parsed.netloc / parsed.path.lstrip("/")
        return Path(unquote(parsed.path))
    return Path(value).expanduser()


class _HybridRow(dict):
    """Dict row with SQLite-compatible integer indexing."""
    def __getitem__(self, key):
        if isinstance(key, int):
            return tuple(self.values())[key]
        return super().__getitem__(key)


class _PgCursor:
    def __init__(self, cursor, buffered_row=None, lastrowid=None):
        self._cursor = cursor
        self._buffered_row = buffered_row
        self.lastrowid = lastrowid
        self.rowcount = cursor.rowcount
        self.description = cursor.description

    def _row(self, row):
        if row is None:
            return None
        names = [column.name for column in (self._cursor.description or [])]
        return _HybridRow(zip(names, row))

    def fetchone(self):
        if self._buffered_row is not None:
            row, self._buffered_row = self._buffered_row, None
            return self._row(row)
        return self._row(self._cursor.fetchone())

    def fetchall(self):
        rows = []
        if self._buffered_row is not None:
            rows.append(self._row(self._buffered_row))
            self._buffered_row = None
        rows.extend(self._row(row) for row in self._cursor.fetchall())
        return rows

    def __iter__(self):
        while True:
            row = self.fetchone()
            if row is None:
                return
            yield row


class _PgConnection:
    """Small DB-API shim for route code written with SQLite-style `?` parameters."""
    def __init__(self, raw):
        self.raw = raw

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        try:
            if exc_type is None:
                self.raw.commit()
            else:
                self.raw.rollback()
        finally:
            self.raw.close()

    def commit(self):
        self.raw.commit()

    def rollback(self):
        self.raw.rollback()

    @staticmethod
    def _params_for_json(sql: str, params: tuple[Any, ...]) -> tuple[Any, ...]:
        if not params or "insert into model_runs" not in sql.lower():
            return params
        try:
            from psycopg.types.json import Jsonb  # type: ignore[import-not-found]
        except Exception:
            return params
        values = list(params)
        for index in (2, 10, 12):  # params, confusion_matrix, details
            if index < len(values) and isinstance(values[index], str):
                try:
                    values[index] = Jsonb(json.loads(values[index]))
                except (json.JSONDecodeError, TypeError):
                    pass
        return tuple(values)

    def execute(self, statement: str, params: Iterable[Any] = ()) -> _PgCursor:
        sql = statement.strip()
        args = tuple(params)
        if re.match(r"(?is)^INSERT\s+OR\s+IGNORE\s+INTO\s+", sql):
            sql = re.sub(r"(?is)^INSERT\s+OR\s+IGNORE\s+INTO\s+", "INSERT INTO ", sql)
            if " on conflict " not in sql.lower():
                sql += " ON CONFLICT DO NOTHING"
        sql = sql.replace("?", "%s")
        table_match = re.match(r"(?is)^INSERT\s+INTO\s+([a-z_][a-z0-9_]*)", sql)
        should_return_id = bool(table_match and table_match.group(1).lower() in _ID_TABLES and " returning " not in sql.lower())
        if should_return_id:
            sql += " RETURNING id"
        args = self._params_for_json(sql, args)
        cursor = self.raw.execute(sql, args)
        first = cursor.fetchone() if should_return_id else None
        lastrowid = first[0] if first is not None else None
        return _PgCursor(cursor, buffered_row=first, lastrowid=lastrowid)

    def executescript(self, script: str) -> None:
        for statement in script.split(";"):
            if statement.strip():
                self.execute(statement)


def get_connection():
    """Open the configured PostGIS or SQLite connection."""
    if is_postgres():
        try:
            import psycopg  # type: ignore[import-not-found]
        except Exception as exc:
            raise RuntimeError("PostgreSQL is configured but psycopg is unavailable; install requirements.txt.") from exc
        return _PgConnection(psycopg.connect(_database_url(), connect_timeout=int(os.getenv("DB_CONNECT_TIMEOUT", "5"))))
    path = _database_path()
    if str(path) != ":memory:":
        path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(str(path), timeout=20, check_same_thread=False)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys=ON")
    connection.execute("PRAGMA busy_timeout=20000")
    return connection


def _schema_sqlite(connection: Any) -> None:
    connection.executescript("""
    PRAGMA foreign_keys=ON;
    CREATE TABLE IF NOT EXISTS wards(ward_id TEXT PRIMARY KEY,name TEXT NOT NULL,geom TEXT NOT NULL,provenance TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS edges(edge_id TEXT PRIMARY KEY,ward_id TEXT NOT NULL,geom TEXT NOT NULL,lst REAL,ndvi REAL,pm REAL,population REAL,slope REAL,ahp_suitability REAL,ahp_risk REAL,geometry_wkt TEXT,start_lat REAL,start_lon REAL,end_lat REAL,end_lon REAL,provenance TEXT NOT NULL,FOREIGN KEY(ward_id) REFERENCES wards(ward_id));
    CREATE TABLE IF NOT EXISTS risk_scores(id INTEGER PRIMARY KEY AUTOINCREMENT,ward_id TEXT NOT NULL,edge_id TEXT,ts TEXT NOT NULL,ml_score REAL,ml_class TEXT,ahp_score REAL,ahp_class TEXT,contributions TEXT,provenance TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS model_runs(id INTEGER PRIMARY KEY AUTOINCREMENT,algo TEXT,trained_at TEXT,params TEXT,accuracy REAL,precision REAL,recall REAL,f1 REAL,rmse REAL,mae REAL,r2 REAL,confusion_matrix TEXT,provenance TEXT NOT NULL,details TEXT);
    CREATE TABLE IF NOT EXISTS alerts(id INTEGER PRIMARY KEY,ward_id TEXT,ts TEXT,severity TEXT,cause TEXT,precaution TEXT,status TEXT,provenance TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS sensor_readings(id INTEGER PRIMARY KEY AUTOINCREMENT,ward_id TEXT,device_id TEXT,ts TEXT,temperature REAL,humidity REAL,quality TEXT,provenance TEXT NOT NULL,event_key TEXT);
    CREATE TABLE IF NOT EXISTS pipeline_runs(id INTEGER PRIMARY KEY AUTOINCREMENT,job TEXT,job_id TEXT,started_at TEXT,finished_at TEXT,status TEXT,log TEXT);
    CREATE TABLE IF NOT EXISTS app_settings(key TEXT PRIMARY KEY,value TEXT NOT NULL);
    CREATE INDEX IF NOT EXISTS idx_edges_ward ON edges(ward_id);
    CREATE INDEX IF NOT EXISTS idx_alerts_status ON alerts(status);
    CREATE INDEX IF NOT EXISTS idx_sensor_ward_ts ON sensor_readings(ward_id,ts);
    CREATE INDEX IF NOT EXISTS idx_risk_ward_ts ON risk_scores(ward_id,ts);
    """)
    columns = {row[1] for row in connection.execute("PRAGMA table_info(pipeline_runs)")}
    if "job_id" not in columns:
        connection.execute("ALTER TABLE pipeline_runs ADD COLUMN job_id TEXT")
    model_columns = {row[1] for row in connection.execute("PRAGMA table_info(model_runs)")}
    if "details" not in model_columns:
        connection.execute("ALTER TABLE model_runs ADD COLUMN details TEXT")
    sensor_columns = {row[1] for row in connection.execute("PRAGMA table_info(sensor_readings)")}
    if "event_key" not in sensor_columns:
        connection.execute("ALTER TABLE sensor_readings ADD COLUMN event_key TEXT")
    connection.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_sensor_event_key_unique ON sensor_readings(event_key)")


def _schema_postgres(connection: Any) -> None:
    schema_path = ROOT / "backend" / "schema.sql"
    if not schema_path.is_file():
        raise RuntimeError(f"PostGIS schema file is missing: {schema_path}")
    connection.executescript(schema_path.read_text(encoding="utf-8"))


def init_db() -> None:
    """Create the selected idempotent database schema."""
    with get_connection() as connection:
        if is_postgres():
            _schema_postgres(connection)
        else:
            _schema_sqlite(connection)
        connection.commit()


def _seed_payload() -> dict[str, Any]:
    json_path = DATA_DIR / "demo_data.json"
    if json_path.exists():
        try:
            payload = json.loads(json_path.read_text(encoding="utf-8"))
            if payload.get("provenance") == "demo_synthetic" and payload.get("wards"):
                return payload
        except (OSError, json.JSONDecodeError):
            pass
    # The reproducible seed function is the source of truth for demo geometry/data.
    import importlib.util
    seed_path = ROOT / "seed_demo.py"
    spec = importlib.util.spec_from_file_location("geocool_seed_demo", seed_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load demo seed generator at {seed_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    payload = module.make_demo()
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return payload


def ensure_demo_seed() -> None:
    """Seed an empty selected database with deterministic, explicitly synthetic records."""
    init_db()
    count = query_one("SELECT COUNT(*) AS n FROM wards") or {"n": 0}
    if int(count.get("n", 0)):
        return
    payload = _seed_payload()
    with get_connection() as connection:
        for ward in payload.get("wards", []):
            if is_postgres():
                connection.execute("INSERT INTO wards(ward_id,name,geom,provenance) VALUES(?,?,ST_SetSRID(ST_GeomFromGeoJSON(?),4326),?) ON CONFLICT(ward_id) DO NOTHING", (ward["ward_id"], ward["name"], json.dumps(ward["geom"]), "demo_synthetic"))
            else:
                connection.execute("INSERT OR IGNORE INTO wards(ward_id,name,geom,provenance) VALUES(?,?,?,?)", (ward["ward_id"], ward["name"], json.dumps(ward["geom"]), "demo_synthetic"))
        for edge in payload.get("edges", []):
            values = (edge["edge_id"], edge["ward_id"], edge.get("lst"), edge.get("ndvi"), edge.get("pm"), edge.get("population"), edge.get("slope"), edge.get("ahp_suitability"), edge.get("ahp_risk"), edge.get("geometry_wkt"), edge.get("start_lat"), edge.get("start_lon"), edge.get("end_lat"), edge.get("end_lon"), "demo_synthetic")
            if is_postgres():
                connection.execute("INSERT INTO edges(edge_id,ward_id,geom,lst,ndvi,pm,population,slope,ahp_suitability,ahp_risk,geometry_wkt,start_lat,start_lon,end_lat,end_lon,provenance) VALUES(?,?,ST_SetSRID(ST_GeomFromGeoJSON(?),4326),?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(edge_id) DO NOTHING", (edge["edge_id"], edge["ward_id"], json.dumps(edge["geom"]), *values[2:]))
            else:
                connection.execute("INSERT OR IGNORE INTO edges(edge_id,ward_id,geom,lst,ndvi,pm,population,slope,ahp_suitability,ahp_risk,geometry_wkt,start_lat,start_lon,end_lat,end_lon,provenance) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (values[0], values[1], json.dumps(edge["geom"]), *values[2:]))
        for alert in payload.get("alerts", []):
            connection.execute("INSERT INTO alerts(id,ward_id,ts,severity,cause,precaution,status,provenance) VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(id) DO NOTHING", (alert["id"], alert["ward_id"], alert["ts"], alert["severity"], alert["cause"], alert["precaution"], alert["status"], "demo_synthetic"))
        for reading in payload.get("sensor_readings", []):
            connection.execute("INSERT INTO sensor_readings(ward_id,device_id,ts,temperature,humidity,quality,provenance) VALUES(?,?,?,?,?,?,?)", (reading["ward_id"], reading["device_id"], reading["ts"], reading["temperature"], reading["humidity"], reading.get("quality", "simulated"), "demo_synthetic"))
        for key, value in (("thresholds", payload.get("thresholds", {"low_max": .25, "moderate_max": .5, "high_max": .75})), ("demo_mode", True)):
            connection.execute("INSERT INTO app_settings(key,value) VALUES(?,?) ON CONFLICT(key) DO NOTHING", (key, json.dumps(value)))
        connection.commit()


def database_health() -> dict[str, Any]:
    engine = "postgresql+postgis" if is_postgres() else "sqlite"
    try:
        count = query_one("SELECT COUNT(*) AS n FROM wards") or {"n": 0}
        demo_count = query_one("SELECT COUNT(*) AS n FROM wards WHERE provenance='demo_synthetic'") or {"n": 0}
        is_demo = int(demo_count.get("n", 0)) > 0
        result = {"connected": True, "engine": engine, "database": "configured PostgreSQL/PostGIS" if is_postgres() else "local SQLite demo database", "ward_count": int(count.get("n", 0)), "demo_ward_count": int(demo_count.get("n", 0)), "demo": is_demo, "provenance": "demo_synthetic" if is_demo else "source_configured"}
        if is_postgres():
            version = query_one("SELECT PostGIS_Full_Version() AS version")
            result["postgis_version"] = version.get("version") if version else None
        return result
    except Exception as exc:
        # Never return a DSN or raw driver exception that could contain a secret.
        return {"connected": False, "engine": engine, "database": "configured PostgreSQL/PostGIS" if is_postgres() else "local SQLite demo database", "error": f"Database health check failed ({type(exc).__name__}); inspect server logs for details.", "demo": False, "provenance": "unavailable"}


def query_all(sql: str, params: Iterable[Any] = ()) -> list[dict[str, Any]]:
    with get_connection() as connection:
        rows = connection.execute(sql, tuple(params)).fetchall()
        return [dict(row) for row in rows]


def query_one(sql: str, params: Iterable[Any] = ()) -> dict[str, Any] | None:
    with get_connection() as connection:
        row = connection.execute(sql, tuple(params)).fetchone()
        return dict(row) if row is not None else None


def execute(sql: str, params: Iterable[Any] = ()) -> int:
    with get_connection() as connection:
        cursor = connection.execute(sql, tuple(params))
        connection.commit()
        return int(getattr(cursor, "lastrowid", None) or cursor.rowcount or 0)


def record_pipeline_run(job: str | dict[str, Any] | None = None, status: str | None = None, log: str = "", started_at: str | None = None, finished_at: str | None = None, job_id: str | None = None, **kwargs: Any) -> int:
    """Persist or update a runner record; accepts its keyword or dict calling style."""
    if isinstance(job, dict):
        payload = {**job, **kwargs}
        job = payload.get("job") or payload.get("job_name")
        status = status or payload.get("status")
        log = payload.get("log", log)
        started_at = payload.get("started_at", started_at)
        finished_at = payload.get("finished_at", finished_at)
        job_id = payload.get("job_id", job_id)
    elif kwargs:
        job = kwargs.get("job", kwargs.get("job_name", job))
        status = kwargs.get("status", status)
        log = kwargs.get("log", log)
        started_at = kwargs.get("started_at", started_at)
        finished_at = kwargs.get("finished_at", finished_at)
        job_id = kwargs.get("job_id", job_id)
    started_at = started_at or datetime.now(timezone.utc).isoformat()
    status = status or "queued"
    with get_connection() as connection:
        if job_id:
            row = connection.execute("SELECT id FROM pipeline_runs WHERE job_id=? ORDER BY id DESC LIMIT 1", (str(job_id),)).fetchone()
            if row:
                record_id = row["id"]
                connection.execute("UPDATE pipeline_runs SET job=?,started_at=COALESCE(?,started_at),finished_at=?,status=?,log=? WHERE id=?", (job, started_at, finished_at, status, log, record_id))
                connection.commit()
                return int(record_id or 0)
        cursor = connection.execute("INSERT INTO pipeline_runs(job,job_id,started_at,finished_at,status,log) VALUES(?,?,?,?,?,?)", (job, str(job_id) if job_id else None, started_at, finished_at, status, log))
        connection.commit()
        return int(getattr(cursor, "lastrowid", None) or cursor.rowcount or 0)


def get_setting(key: str, default: Any = None) -> Any:
    row = query_one("SELECT value FROM app_settings WHERE key=?", (key,))
    if row is None:
        return default
    try:
        return json.loads(row["value"])
    except (TypeError, json.JSONDecodeError):
        return row["value"]


def set_setting(key: str, value: Any) -> None:
    execute("INSERT INTO app_settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, json.dumps(value)))


def load_demo_payload() -> dict[str, Any]:
    """Return the reproducible JSON payload, generating it if absent."""
    return _seed_payload()
