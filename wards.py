"""Ward map, street, sensor-history and numeric explanation endpoints."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from fastapi import APIRouter, HTTPException, Query

from backend.db import get_setting, is_postgres, query_all, query_one

router = APIRouter()
WEIGHTS = {"lst": 0.35, "ndvi": 0.25, "pm": 0.20, "population": 0.10, "slope": 0.10}
FEATURES = tuple(WEIGHTS)


def _json(value, fallback=None):
    if value is None:
        return fallback
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return fallback


def _risk_class(value: float | None, thresholds=None) -> str | None:
    if value is None:
        return None
    t = thresholds or {"low_max": .25, "moderate_max": .5, "high_max": .75}
    if value < t["low_max"]:
        return "Low"
    if value < t["moderate_max"]:
        return "Moderate"
    if value < t["high_max"]:
        return "High"
    return "Critical"


def _geom_select() -> str:
    return "ST_AsGeoJSON(geom) AS geom" if is_postgres() else "geom"


def _ward_stats(ward_id: str) -> dict:
    """Aggregate intersecting edges into a ward using PostGIS for database deployments."""
    if is_postgres():
        result = query_one("""
            SELECT AVG(e.ahp_risk) AS mean_risk,
                   MAX(e.ahp_risk) AS max_risk,
                   percentile_cont(0.90) WITHIN GROUP (ORDER BY e.ahp_risk) AS p90_risk,
                   AVG(e.lst) AS mean_lst, AVG(e.ndvi) AS mean_ndvi,
                   AVG(e.pm) AS mean_pm, AVG(e.population) AS mean_population,
                   AVG(e.slope) AS mean_slope, COUNT(e.edge_id) AS edge_count
            FROM wards w
            LEFT JOIN edges e ON ST_Intersects(w.geom, e.geom)
            WHERE w.ward_id=?
        """, (ward_id,)) or {}
    else:
        result = query_one("""
            SELECT AVG(ahp_risk) AS mean_risk, MAX(ahp_risk) AS max_risk,
                   AVG(lst) AS mean_lst, AVG(ndvi) AS mean_ndvi,
                   AVG(pm) AS mean_pm, AVG(population) AS mean_population,
                   AVG(slope) AS mean_slope, COUNT(edge_id) AS edge_count
            FROM edges WHERE ward_id=?
        """, (ward_id,)) or {}
        if result.get("mean_risk") is not None:
            risks = [float(row["ahp_risk"]) for row in query_all("SELECT ahp_risk FROM edges WHERE ward_id=? AND ahp_risk IS NOT NULL ORDER BY ahp_risk", (ward_id,))]
            result["p90_risk"] = risks[min(len(risks) - 1, max(0, int(__import__("math").ceil(.9 * len(risks))) - 1))] if risks else None
        else:
            result["p90_risk"] = None
        if result.get("max_risk") is None:
            result["max_risk"] = None
    return result


def _ward_risk(ward_id: str, fallback_ahp: float | None) -> dict:
    latest = query_one("SELECT ahp_score AS ahp, ml_score AS ml FROM risk_scores WHERE ward_id=? ORDER BY ts DESC,id DESC LIMIT 1", (ward_id,))
    if latest:
        ahp = float(latest["ahp"]) if latest.get("ahp") is not None else fallback_ahp
        ml = float(latest["ml"]) if latest.get("ml") is not None else None
        if ml is not None or ahp is not None:
            return {"ahp_score": ahp, "ml_score": ml, "score_basis": "Recorded trained-model output" if ml is not None else "AHP-derived reference only; no trained ML prediction is recorded"}
    return {"ahp_score": fallback_ahp, "ml_score": None, "score_basis": "AHP-inverted suitability is shown as the weak-label reference; no trained ML output is available"}


def _ward_record(row: dict) -> dict:
    ward_id = row["ward_id"]
    geom = _json(row.get("geom"), {})
    stats = _ward_stats(ward_id)
    scores = _ward_risk(ward_id, stats.get("mean_risk"))
    ahp = scores.get("ahp_score")
    ml = scores.get("ml_score")
    class_score = ml if ml is not None else ahp
    thresholds = get_setting("thresholds", {"low_max": .25, "moderate_max": .5, "high_max": .75})
    center = None
    try:
        ring = geom.get("coordinates", [[]])[0]
        if ring:
            points = ring[:-1] or ring
            center = {"lat": sum(p[1] for p in points) / len(points), "lon": sum(p[0] for p in points) / len(points)}
    except (AttributeError, TypeError, IndexError):
        pass
    provenance = row.get("provenance") or "unknown"
    return {
        "ward_id": ward_id,
        "name": row["name"],
        "geom": geom,
        "geometry": geom,
        "center": center,
        "edge_count": int(stats.get("edge_count") or 0),
        "ml_score": round(ml, 4) if ml is not None else None,
        "ml_class": _risk_class(ml, thresholds),
        "ahp_score": round(ahp, 4) if ahp is not None else None,
        "ahp_suitability": round(1.0 - float(ahp), 4) if ahp is not None else None,
        "ahp_class": _risk_class(ahp, thresholds),
        "risk_class": _risk_class(class_score, thresholds),
        "class_basis": "trained model" if ml is not None else "AHP-derived weak label; no ML model output",
        "mean_risk": round(float(stats["mean_risk"]), 4) if stats.get("mean_risk") is not None else None,
        "max_risk": round(float(stats["max_risk"]), 4) if stats.get("max_risk") is not None else None,
        "p90_risk": round(float(stats["p90_risk"]), 4) if stats.get("p90_risk") is not None else None,
        "mean_lst": stats.get("mean_lst"), "mean_ndvi": stats.get("mean_ndvi"),
        "mean_pm": stats.get("mean_pm"), "mean_population": stats.get("mean_population"),
        "mean_slope": stats.get("mean_slope"),
        "provenance": provenance,
        "demo": provenance == "demo_synthetic",
        "score_basis": scores["score_basis"],
    }


@router.get("/wards")
def wards():
    records = [_ward_record(r) for r in query_all(f"SELECT ward_id,name,{_geom_select()},provenance FROM wards ORDER BY ward_id")]
    if not records:
        raise HTTPException(status_code=503, detail="Ward data is missing. Run seed_demo.py or load valid ward polygons.")
    alert_count = query_one("SELECT COUNT(*) AS n FROM alerts WHERE status IN ('open','ack')") or {"n": 0}
    newest = query_one("SELECT MAX(ts) AS ts FROM sensor_readings") or {}
    run = query_one("SELECT MAX(f1) AS f1 FROM model_runs") or {}
    demo = any(w["demo"] for w in records)
    return {
        "wards": records,
        "demo": demo,
        "label": "DEMO DATA — synthetic ward geometries and seeded features" if demo else "Ward data from configured source",
        "summary": {
            "ward_count": len(records),
            "critical_count": sum(w["risk_class"] == "Critical" for w in records),
            "active_alerts": int(alert_count["n"]),
            "last_refresh": get_setting("last_refresh", None) or newest.get("ts") or datetime.now(timezone.utc).isoformat(),
            "model_f1": run.get("f1"),
            "provenance": "demo_synthetic" if demo else "source_configured",
        },
    }


@router.get("/edges")
def all_edges(limit: int = Query(default=2000, ge=1, le=10000)):
    rows = query_all(f"SELECT edge_id,ward_id,{_geom_select()},lst,ndvi,pm,population,slope,ahp_suitability,ahp_risk,geometry_wkt,provenance FROM edges ORDER BY edge_id LIMIT ?", (limit,))
    thresholds = get_setting("thresholds", None)
    for row in rows:
        row["geometry"] = _json(row.pop("geom"), {})
        row["risk_class"] = _risk_class(float(row.get("ahp_risk") or 0), thresholds)
        row["ahp_class"] = row["risk_class"]
        row["ml_score"] = None
        row["ml_class"] = None
        row["demo"] = row.get("provenance") == "demo_synthetic"
    return {"edges": rows, "count": len(rows), "limit": limit, "demo": any(row["demo"] for row in rows), "provenance": "demo_synthetic" if any(row["demo"] for row in rows) else ("source_configured" if rows else "no_data")}


@router.get("/wards/{ward_id}")
def ward_detail(ward_id: str):
    row = query_one(f"SELECT ward_id,name,{_geom_select()},provenance FROM wards WHERE ward_id=?", (ward_id,))
    if not row:
        raise HTTPException(status_code=404, detail=f"Ward {ward_id!r} was not found.")
    result = _ward_record(row)
    result["features"] = {key: result.get(f"mean_{key}") for key in FEATURES}
    result["alerts"] = query_all("SELECT * FROM alerts WHERE ward_id=? ORDER BY ts DESC", (ward_id,))
    result["demo_label"] = "Synthetic demonstration data; not official ward boundaries or field measurements." if result["demo"] else "Values reflect the configured source; verify definitions and coverage."
    return result


def _edge_explanation(ward_id: str, edge: dict, thresholds=None) -> dict:
    thresholds = thresholds or get_setting("thresholds", {"low_max": .25, "moderate_max": .5, "high_max": .75})
    limits = query_all("SELECT MIN(lst) AS lst_min,MAX(lst) AS lst_max,MIN(ndvi) AS ndvi_min,MAX(ndvi) AS ndvi_max,MIN(pm) AS pm_min,MAX(pm) AS pm_max,MIN(population) AS population_min,MAX(population) AS population_max,MIN(slope) AS slope_min,MAX(slope) AS slope_max FROM edges")
    bounds = limits[0] if limits else {}
    contributions = []
    for feature, weight in WEIGHTS.items():
        value = float(edge.get(feature) or 0.0)
        low = float(bounds.get(f"{feature}_min") or 0.0)
        high = float(bounds.get(f"{feature}_max") or 0.0)
        normalized = (value - low) / (high - low) if high != low else 0.0
        # Signed, exact weighted change from a normalized 0.5 reference; all
        # components sum to risk - 0.5. PM is zero-information when constant.
        signed = (0.5 - normalized if feature == "ndvi" else normalized - 0.5) * weight
        constant = high == low
        if constant:
            signed = 0.0
        contributions.append({"feature": feature, "value": value, "normalized": round(normalized, 6), "weight": weight, "contribution": round(signed, 6), "direction": "increases risk" if signed > 0 else "decreases risk" if signed < 0 else "neutral", "constant_input": constant})
    top = sorted(contributions, key=lambda item: abs(item["contribution"]), reverse=True)[:3]
    risk = float(edge.get("ahp_risk") or 0.0)
    ward_scores = _ward_risk(ward_id, None)
    ward_ml_score = ward_scores.get("ml_score")
    parts = ", ".join(f"{item['feature']} {item['direction']} by {item['contribution']:+.3f}" for item in top)
    sentence = f"AHP reference risk is {risk:.3f}; the top weighted factors relative to mid-range are {parts}. This is not a trained ML prediction or observed ground truth."
    return {
        "ward_id": ward_id,
        "edge_id": edge.get("edge_id"),
        "ml_score": None,
        "ml_class": None,
        "ward_ml_score": round(float(ward_ml_score), 4) if ward_ml_score is not None else None,
        "ward_ml_class": _risk_class(ward_ml_score, thresholds),
        "ahp_score": round(risk, 4),
        "ahp_class": _risk_class(risk, thresholds),
        "risk_class": edge.get("risk_class") or _risk_class(risk, thresholds),
        "score_basis": "AHP-derived street reference; no edge-level trained ML score is stored",
        "contributions": top,
        "feature_details": contributions,
        "explanation": sentence,
        "weights": WEIGHTS,
        "constant_inputs": {"pm": {"constant": float(bounds.get("pm_min") or 0) == float(bounds.get("pm_max") or 0), "value": edge.get("pm"), "warning": "PM is a constant placeholder in the demo; it is not a measured driver."}},
        "weak_label": "AHP-inverted suitability threshold; not field ground truth",
        "methodology": "signed AHP contribution relative to normalized midpoint",
        "demo": edge.get("provenance") == "demo_synthetic",
        "provenance": edge.get("provenance"),
    }


@router.get("/wards/{ward_id}/streets")
def ward_streets(ward_id: str):
    if not query_one("SELECT ward_id FROM wards WHERE ward_id=?", (ward_id,)):
        raise HTTPException(status_code=404, detail=f"Ward {ward_id!r} was not found.")
    rows = query_all(f"SELECT edge_id,ward_id,{_geom_select()},lst,ndvi,pm,population,slope,ahp_suitability,ahp_risk,geometry_wkt,start_lat,start_lon,end_lat,end_lon,provenance FROM edges WHERE ward_id=? ORDER BY edge_id", (ward_id,))
    thresholds = get_setting("thresholds", None)
    for row in rows:
        row["geometry"] = _json(row.pop("geom"), {})
        row["risk_class"] = _risk_class(float(row.get("ahp_risk") or 0), thresholds)
        row["ahp_class"] = row["risk_class"]
        row["ml_score"] = None
        row["ml_class"] = None
        row["demo"] = row.get("provenance") == "demo_synthetic"
        row["explanation"] = _edge_explanation(ward_id, row, thresholds)
    return {"ward_id": ward_id, "streets": rows, "edges": rows, "count": len(rows), "demo": any(e["demo"] for e in rows), "provenance": "demo_synthetic" if any(e["demo"] for e in rows) else "source_configured"}


@router.get("/wards/{ward_id}/sensor-history")
def sensor_history(ward_id: str, limit: int = 100):
    if not query_one("SELECT ward_id FROM wards WHERE ward_id=?", (ward_id,)):
        raise HTTPException(status_code=404, detail=f"Ward {ward_id!r} was not found.")
    limit = max(1, min(limit, 1000))
    rows = query_all("SELECT id,ward_id,device_id,ts,temperature,humidity,quality,provenance FROM sensor_readings WHERE ward_id=? ORDER BY ts DESC LIMIT ?", (ward_id, limit))
    return {"ward_id": ward_id, "readings": rows, "history": rows, "count": len(rows), "demo": any(row.get("provenance") == "demo_synthetic" for row in rows), "provenance": "demo_synthetic" if any(row.get("provenance") == "demo_synthetic" for row in rows) else "source_configured"}


@router.get("/explain/{ward_id}")
def explain_ward(ward_id: str, edge_id: str | None = Query(default=None)):
    ward = query_one("SELECT ward_id,provenance FROM wards WHERE ward_id=?", (ward_id,))
    if not ward:
        raise HTTPException(status_code=404, detail=f"Ward {ward_id!r} was not found.")
    thresholds = get_setting("thresholds", {"low_max": .25, "moderate_max": .5, "high_max": .75})
    if edge_id:
        edge = query_one("SELECT edge_id,ward_id,lst,ndvi,pm,population,slope,ahp_risk,provenance FROM edges WHERE ward_id=? AND edge_id=?", (ward_id, edge_id))
        if not edge:
            raise HTTPException(status_code=404, detail=f"Street edge {edge_id!r} was not found in ward {ward_id!r}.")
        edge["risk_class"] = _risk_class(float(edge.get("ahp_risk") or 0), thresholds)
        return _edge_explanation(ward_id, edge, thresholds)

    records = query_all("SELECT lst,ndvi,pm,population,slope,ahp_risk FROM edges WHERE ward_id=?", (ward_id,))
    if not records:
        raise HTTPException(status_code=404, detail=f"No feature rows are available for {ward_id!r}.")
    means = {feature: sum(float(row[feature] or 0) for row in records) / len(records) for feature in FEATURES}
    limits = query_one("SELECT MIN(lst) AS lst_min,MAX(lst) AS lst_max,MIN(ndvi) AS ndvi_min,MAX(ndvi) AS ndvi_max,MIN(pm) AS pm_min,MAX(pm) AS pm_max,MIN(population) AS population_min,MAX(population) AS population_max,MIN(slope) AS slope_min,MAX(slope) AS slope_max FROM edges") or {}
    contributions = []
    for feature, weight in WEIGHTS.items():
        lo = float(limits.get(f"{feature}_min") or 0.0)
        hi = float(limits.get(f"{feature}_max") or 0.0)
        normalized = (means[feature] - lo) / (hi - lo) if hi != lo else 0.0
        contribution = (normalized if feature in {"lst", "pm", "population", "slope"} else 1 - normalized) * weight
        contributions.append({"feature": feature, "contribution": round(contribution, 6), "value": round(means[feature], 5), "normalized": round(normalized, 5), "weight": weight, "direction": "increases risk" if contribution > 0 else "decreases risk"})
    scores = _ward_risk(ward_id, sum(float(row["ahp_risk"] or 0) for row in records) / len(records))
    ahp_score, ml_score = scores.get("ahp_score"), scores.get("ml_score")
    explanation = "AHP risk is the inverse of weighted suitability. The listed terms are transparent weighted contributions based on global MinMax normalization. AHP-derived risk classes are weak labels, not observed ground truth. PM is a constant 65.0 placeholder, not a sensor measurement."
    method = "transparent_weighted_AHP_contributions_not_SHAP"
    model_demo = False
    try:
        from backend.ml.explain import explain_ward as explain_model
        candidate = explain_model(ward_id)
        if isinstance(candidate, dict) and candidate.get("available"):
            contributions = candidate.get("contributions", contributions)
            explanation = candidate.get("explanation", explanation)
            ml_score = candidate.get("predicted_risk", ml_score)
            method = candidate.get("method", "trained_model_explanation")
            model_demo = bool(candidate.get("demo")) or "demo" in str(candidate.get("provenance", "")).lower() or "synthetic" in str(candidate.get("provenance", "")).lower()
    except Exception:
        pass
    demo_edges = query_one("SELECT COUNT(*) AS n FROM edges WHERE ward_id=? AND provenance='demo_synthetic'", (ward_id,)) or {"n": 0}
    is_demo = model_demo or ward.get("provenance") == "demo_synthetic" or int(demo_edges.get("n", 0) or 0) > 0
    data_provenance = "demo_synthetic" if is_demo else str(ward.get("provenance") or "source_configured")
    class_score = ml_score if ml_score is not None else ahp_score
    return {
        "ward_id": ward_id,
        "ml_score": round(float(ml_score), 4) if ml_score is not None else None,
        "ahp_score": round(float(ahp_score), 4) if ahp_score is not None else None,
        "risk_class": _risk_class(class_score, thresholds),
        "ml_class": _risk_class(ml_score, thresholds),
        "ahp_class": _risk_class(ahp_score, thresholds),
        "score_basis": scores["score_basis"],
        "contributions": contributions,
        "feature_details": contributions,
        "explanation": explanation,
        "weights": WEIGHTS,
        "constant_inputs": {"pm": {"constant": float(limits.get("pm_min") or 0) == float(limits.get("pm_max") or 0), "value": means["pm"], "warning": "PM is constant in demo records and is not a measured driver."}},
        "weak_label": "Labels are derived from AHP-inverted risk thresholds, not field ground truth.",
        "methodology": method,
        "demo": is_demo,
        "provenance": data_provenance,
    }
