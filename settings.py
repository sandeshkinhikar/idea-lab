"""Persistent risk settings, data inventory, and source-file refresh endpoints."""
from __future__ import annotations

import csv
import json
import math
from datetime import datetime, timezone
from pathlib import Path
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field, model_validator

from backend.db import ROOT, get_connection, get_setting, is_postgres, query_all, query_one, set_setting

router = APIRouter()
DEFAULTS = {"low_max": 0.25, "moderate_max": 0.5, "high_max": 0.75, "temperature_max": 35.0}
RASTERS = [
    ("nagpur_lst.tif", "scripts/1_download_rasters.py", "Landsat 8 LST"),
    ("nagpur_ndvi.tif", "scripts/1_download_rasters.py", "Sentinel-2 NDVI"),
    ("nagpur_slope.tif", "scripts/1_download_rasters.py", "SRTM slope"),
    ("nagpur_pop.tif", "scripts/1_download_rasters.py", "WorldPop population"),
]
FEATURES = ("lst", "ndvi", "pm", "population", "slope")
WEIGHTS = {"lst": 0.35, "ndvi": 0.25, "pm": 0.20, "population": 0.10, "slope": 0.10}


def _risk_class(score: float, thresholds: dict) -> str:
    if score < thresholds["low_max"]:
        return "Low"
    if score < thresholds["moderate_max"]:
        return "Moderate"
    if score < thresholds["high_max"]:
        return "High"
    return "Critical"


class Thresholds(BaseModel):
    low_max: float = Field(ge=0, le=1)
    moderate_max: float = Field(ge=0, le=1)
    high_max: float = Field(ge=0, le=1)
    temperature_max: float = Field(default=35.0, ge=-30, le=80)

    @model_validator(mode="after")
    def ordered(self):
        if not self.low_max < self.moderate_max < self.high_max:
            raise ValueError("Require low_max < moderate_max < high_max.")
        return self


class DemoModeRequest(BaseModel):
    enabled: bool | None = None
    demo_mode: bool | None = None

    @model_validator(mode="after")
    def require_value(self):
        if self.enabled is None and self.demo_mode is None:
            raise ValueError("Provide either enabled or demo_mode as a boolean.")
        return self


@router.get("/settings/thresholds")
def read_thresholds():
    value = get_setting("thresholds", DEFAULTS)
    return {"thresholds": {**DEFAULTS, **value}, "provenance": "application_setting"}


@router.put("/settings/thresholds")
def update_thresholds(body: Thresholds):
    value = body.model_dump()
    try:
        set_setting("thresholds", value)
        from backend.ml.train import _thresholds
        _thresholds.cache_clear()
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Could not persist threshold settings: {exc}") from exc
    return {"thresholds": value, "reclassified": True, "message": "Ward and street risk classes are calculated from these thresholds on each request; base scores remain unchanged.", "provenance": "application_setting"}


@router.get("/settings/demo-mode")
def read_demo_mode():
    enabled = bool(get_setting("demo_mode", True))
    return {"demo_mode": enabled, "enabled": enabled, "provenance": "demo_synthetic", "data_label": "DEMO DATA", "notice": "Changing Demo Mode affects the application mode only; it does not convert, relabel, or claim any synthetic record is measured."}


@router.put("/settings/demo-mode")
def update_demo_mode(body: DemoModeRequest):
    enabled = body.enabled if body.enabled is not None else bool(body.demo_mode)
    try:
        set_setting("demo_mode", enabled)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Could not persist Demo Mode setting: {exc}") from exc
    return {"demo_mode": enabled, "enabled": enabled, "provenance": "demo_synthetic", "data_label": "DEMO DATA", "notice": "Existing rows retain their provenance; toggling this setting does not make synthetic data real or measured."}


@router.get("/data/files")
def data_files():
    data_root = ROOT / "data"
    rasters = []
    for filename, producer, product in RASTERS:
        path = data_root / "raw" / filename
        if path.is_file():
            stat = path.stat()
            record = {"name": filename, "path": str(path.relative_to(ROOT)), "status": "available", "exists": True, "size": stat.st_size, "size_bytes": stat.st_size, "modified": datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat(), "producer": producer, "product": product, "provenance": "source_file"}
        else:
            record = {"name": filename, "path": str(path.relative_to(ROOT)), "status": "missing", "exists": False, "size": None, "size_bytes": None, "modified": None, "producer": producer, "product": product, "provenance": "not_available"}
        rasters.append(record)
    csv_path = data_root / "processed" / "edge_suitability.csv"
    preview, columns, rows = [], [], 0
    if csv_path.is_file():
        try:
            with csv_path.open(newline="", encoding="utf-8-sig") as handle:
                reader = csv.DictReader(handle)
                columns = reader.fieldnames or []
                for row in reader:
                    rows += 1
                    if len(preview) < 50:
                        preview.append(row)
        except (OSError, csv.Error) as exc:
            raise HTTPException(status_code=500, detail=f"Could not read {csv_path.relative_to(ROOT)}: {exc}") from exc
    demo_edge_ids: set[str] = set()
    demo_path = data_root / "demo_data.json"
    if demo_path.is_file():
        try:
            demo_payload = json.loads(demo_path.read_text(encoding="utf-8"))
            if demo_payload.get("provenance") == "demo_synthetic":
                demo_edge_ids = {str(edge.get("edge_id")) for edge in demo_payload.get("edges", []) if edge.get("edge_id") is not None}
        except (OSError, json.JSONDecodeError, AttributeError):
            demo_edge_ids = set()
    is_demo_csv = bool(demo_edge_ids and any(str(row.get("edge_id", "")) in demo_edge_ids for row in preview))
    csv_info = {"path": str(csv_path.relative_to(ROOT)), "status": "available" if csv_path.is_file() else "missing", "row_count": rows, "columns": columns, "preview": preview, "provenance": "demo_synthetic" if is_demo_csv else ("source_file" if csv_path.is_file() else "not_available")}
    return {"rasters": rasters, "tiffs": rasters, "csv": csv_info, "csv_preview": {"columns": columns, "rows": preview, "row_count": rows}, "columns": columns, "row_count": rows, "demo": csv_info["provenance"] == "demo_synthetic", "label": "DEMO DATA — generated synthetic seed rows are labeled; absent rasters and unassigned real features are reported, never inferred."}


def _read_sources(json_path: Path, csv_path: Path):
    payload = None
    if json_path.is_file():
        try:
            candidate = json.loads(json_path.read_text(encoding="utf-8"))
            if isinstance(candidate, dict) and isinstance(candidate.get("wards"), list) and isinstance(candidate.get("edges"), list):
                payload = candidate
        except (OSError, json.JSONDecodeError) as exc:
            raise HTTPException(status_code=422, detail=f"Could not read {json_path.relative_to(ROOT)}: {exc}. Correct the source file; no database rows were changed.") from exc
    rows, columns = [], []
    if csv_path.is_file():
        try:
            with csv_path.open(newline="", encoding="utf-8-sig") as handle:
                reader = csv.DictReader(handle)
                columns = reader.fieldnames or []
                rows = list(reader)
        except (OSError, csv.Error) as exc:
            raise HTTPException(status_code=422, detail=f"Could not read {csv_path.relative_to(ROOT)}: {exc}. Correct the source file; no database rows were changed.") from exc
    # A stale seeded JSON must never overwrite a real OSMnx CSV whose edge IDs differ.
    if payload and payload.get("provenance") == "demo_synthetic" and rows:
        seed_ids = {str(edge.get("edge_id")) for edge in payload["edges"] if edge.get("edge_id") is not None}
        csv_ids = {str(row.get("edge_id")) for row in rows if row.get("edge_id")}
        if not seed_ids.intersection(csv_ids):
            payload = None
    return payload, rows, columns


def _map_real_csv_to_wards(rows: list[dict], existing_by_id: dict[str, dict]) -> list[dict]:
    mapped = []
    for raw in rows:
        edge_id = str(raw.get("edge_id") or "").strip()
        if not edge_id:
            continue
        existing = existing_by_id.get(edge_id)
        ward_id = raw.get("ward_id") or (existing or {}).get("ward_id")
        geometry_wkt = raw.get("geometry_wkt") or (existing or {}).get("geometry_wkt")
        if not ward_id and is_postgres() and geometry_wkt:
            match = query_one("SELECT ward_id FROM wards WHERE provenance <> 'demo_synthetic' AND ST_Intersects(geom,ST_SetSRID(ST_GeomFromText(?),4326)) ORDER BY ward_id LIMIT 1", (geometry_wkt,))
            ward_id = match.get("ward_id") if match else None
        if not ward_id or not geometry_wkt:
            continue
        item = dict(existing or {})
        item.update(raw)
        item["edge_id"] = edge_id
        item["ward_id"] = str(ward_id)
        item["geometry_wkt"] = geometry_wkt
        if is_postgres():
            feature = query_one("SELECT ST_AsGeoJSON(ST_SetSRID(ST_GeomFromText(?),4326)) AS geom", (geometry_wkt,))
            item["geom"] = feature.get("geom") if feature else None
        elif existing and existing.get("geom"):
            try:
                item["geom"] = json.loads(existing["geom"]) if isinstance(existing["geom"], str) else existing["geom"]
            except json.JSONDecodeError:
                item["geom"] = None
        if item.get("geom") is not None:
            mapped.append(item)
    return mapped


@router.post("/refresh")
def refresh_sources():
    """Reload only existing JSON/CSV records, then recompute the exact inverted-AHP score."""
    json_path = ROOT / "data" / "demo_data.json"
    csv_path = ROOT / "data" / "processed" / "edge_suitability.csv"
    payload, csv_rows, csv_columns = _read_sources(json_path, csv_path)
    source_provenance = (payload.get("provenance") or ("demo_synthetic" if payload.get("demo") else "source_unspecified")) if payload else ("real_dataset" if csv_rows else "source_unspecified")
    wards = payload.get("wards", []) if payload else []
    edge_source = payload.get("edges", []) if payload else []

    geom_select = "ST_AsGeoJSON(geom) AS geom" if is_postgres() else "geom"
    existing_edges = query_all(f"SELECT edge_id,ward_id,{geom_select},lst,ndvi,pm,population,slope,ahp_suitability,ahp_risk,geometry_wkt,start_lat,start_lon,end_lat,end_lon,provenance FROM edges")
    existing_by_id = {str(row["edge_id"]): row for row in existing_edges}
    input_by_id = {str(row.get("edge_id")): dict(row) for row in edge_source if row.get("edge_id") is not None}

    if payload:
        for raw in csv_rows:
            edge_id = str(raw.get("edge_id") or "")
            base = input_by_id.get(edge_id) or existing_by_id.get(edge_id)
            if not edge_id or base is None:
                continue
            for feature in FEATURES:
                if raw.get(feature) not in (None, ""):
                    try:
                        base[feature] = float(raw[feature])
                    except (TypeError, ValueError):
                        raise HTTPException(status_code=422, detail=f"CSV value for {feature!r} on edge {edge_id!r} is not numeric; no database rows were changed.")
            for key in ("geometry_wkt", "start_lat", "start_lon", "end_lat", "end_lon"):
                if raw.get(key) not in (None, ""):
                    base[key] = raw[key]
            input_by_id[edge_id] = base
    elif csv_rows:
        input_by_id = {str(row["edge_id"]): dict(row) for row in _map_real_csv_to_wards(csv_rows, existing_by_id)}

    edges = list(input_by_id.values())
    if not edges and not wards:
        reason = "The CSV is present but its edge IDs do not match existing rows and no official non-demo ward polygons are available for a PostGIS ST_Intersects join. The source CSV remains available for preview; no synthetic ward assignment or database update was made." if csv_rows else "No usable existing source records were found. Nothing was changed; add a valid demo JSON payload or load real polygons and geometry-aligned edge CSV."
        return {"refreshed": False, "sources": {"json": "loaded" if payload else "missing_or_not_mapped", "csv": "loaded_unassigned" if csv_rows else "missing", "csv_columns": csv_columns}, "updated": {"wards": 0, "edges": 0}, "message": reason, "demo": source_provenance == "demo_synthetic", "provenance": source_provenance}

    feature_values = {}
    if edges:
        for feature in FEATURES:
            values = []
            for item in edges:
                try:
                    value = float(item[feature])
                    if not math.isfinite(value):
                        raise ValueError
                    values.append(value)
                except (KeyError, TypeError, ValueError):
                    raise HTTPException(status_code=422, detail=f"Input edge is missing finite numeric feature {feature!r}; no database rows were changed.")
            feature_values[feature] = (min(values), max(values))
    recalculated = []
    for item in edges:
        normalized = {}
        for feature, (low, high) in feature_values.items():
            value = float(item[feature])
            normalized[feature] = (value - low) / (high - low) if high != low else 0.0
        suitability = (1 - normalized["lst"]) * .35 + normalized["ndvi"] * .25 + (1 - normalized["pm"]) * .20 + (1 - normalized["population"]) * .10 + (1 - normalized["slope"]) * .10
        recalculated.append((item, suitability, 1 - suitability))

    current_thresholds = {**DEFAULTS, **get_setting("thresholds", {})}
    with get_connection() as connection:
        updated_wards = 0
        for ward in wards:
            if not ward.get("ward_id") or not ward.get("name") or not isinstance(ward.get("geom"), dict):
                continue
            ward_provenance = ward.get("provenance") or source_provenance
            if is_postgres():
                connection.execute("INSERT INTO wards(ward_id,name,geom,provenance) VALUES(?,?,ST_SetSRID(ST_GeomFromGeoJSON(?),4326),?) ON CONFLICT(ward_id) DO UPDATE SET name=excluded.name,geom=excluded.geom,provenance=excluded.provenance", (ward["ward_id"], ward["name"], json.dumps(ward["geom"]), ward_provenance))
            else:
                connection.execute("INSERT INTO wards(ward_id,name,geom,provenance) VALUES(?,?,?,?) ON CONFLICT(ward_id) DO UPDATE SET name=excluded.name,geom=excluded.geom,provenance=excluded.provenance", (ward["ward_id"], ward["name"], json.dumps(ward["geom"]), ward_provenance))
            updated_wards += 1
        updated_edges = 0
        for item, suitability, risk in recalculated:
            ward_id = item.get("ward_id") or existing_by_id.get(str(item.get("edge_id")), {}).get("ward_id")
            if not ward_id:
                continue
            geom = item.get("geom")
            if isinstance(geom, str):
                try:
                    geom = json.loads(geom)
                except json.JSONDecodeError:
                    # PostGIS JSON text is passed directly to ST_GeomFromGeoJSON below.
                    if not is_postgres():
                        geom = None
            edge_provenance = item.get("provenance") or source_provenance
            values = (item["edge_id"], ward_id, item.get("lst"), item.get("ndvi"), item.get("pm"), item.get("population"), item.get("slope"), suitability, risk, item.get("geometry_wkt"), item.get("start_lat"), item.get("start_lon"), item.get("end_lat"), item.get("end_lon"), edge_provenance)
            if is_postgres():
                geometry_json = json.dumps(geom) if isinstance(geom, dict) else str(item.get("geom") or "")
                if not geometry_json:
                    continue
                statement = "INSERT INTO edges(edge_id,ward_id,geom,lst,ndvi,pm,population,slope,ahp_suitability,ahp_risk,geometry_wkt,start_lat,start_lon,end_lat,end_lon,provenance) VALUES(?,?,ST_SetSRID(ST_GeomFromGeoJSON(?),4326),?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(edge_id) DO UPDATE SET ward_id=excluded.ward_id,geom=excluded.geom,lst=excluded.lst,ndvi=excluded.ndvi,pm=excluded.pm,population=excluded.population,slope=excluded.slope,ahp_suitability=excluded.ahp_suitability,ahp_risk=excluded.ahp_risk,geometry_wkt=excluded.geometry_wkt,start_lat=excluded.start_lat,start_lon=excluded.start_lon,end_lat=excluded.end_lat,end_lon=excluded.end_lon,provenance=excluded.provenance"
                connection.execute(statement, (values[0], values[1], geometry_json, *values[2:]))
            else:
                if not isinstance(geom, dict):
                    continue
                statement = "INSERT INTO edges(edge_id,ward_id,geom,lst,ndvi,pm,population,slope,ahp_suitability,ahp_risk,geometry_wkt,start_lat,start_lon,end_lat,end_lon,provenance) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(edge_id) DO UPDATE SET ward_id=excluded.ward_id,geom=excluded.geom,lst=excluded.lst,ndvi=excluded.ndvi,pm=excluded.pm,population=excluded.population,slope=excluded.slope,ahp_suitability=excluded.ahp_suitability,ahp_risk=excluded.ahp_risk,geometry_wkt=excluded.geometry_wkt,start_lat=excluded.start_lat,start_lon=excluded.start_lon,end_lat=excluded.end_lat,end_lon=excluded.end_lon,provenance=excluded.provenance"
                connection.execute(statement, (values[0], values[1], json.dumps(geom), *values[2:]))
            connection.execute("UPDATE risk_scores SET ahp_score=?,ahp_class=? WHERE edge_id=?", (risk, _risk_class(risk, current_thresholds), item["edge_id"]))
            updated_edges += 1
        connection.commit()
    refreshed_at = datetime.now(timezone.utc).isoformat()
    set_setting("last_refresh", refreshed_at)
    return {"refreshed": True, "refreshed_at": refreshed_at, "sources": {"json": "loaded" if payload else "not_used", "csv": "loaded" if csv_rows else "missing", "csv_columns": csv_columns}, "updated": {"wards": updated_wards, "edges": updated_edges}, "recomputed": "AHP suitability and inverse risk using exact weights lst .35, ndvi .25, pm .20, population .10, slope .10 with MinMax normalization over the loaded edge records.", "demo": source_provenance == "demo_synthetic", "provenance": source_provenance, "message": "Only rows already present in the source payload or spatially matched to configured non-demo PostGIS ward polygons were refreshed; no values or geometries were fabricated."}
