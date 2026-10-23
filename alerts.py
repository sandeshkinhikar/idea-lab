"""Alert lifecycle and explicitly synthetic sensor simulation."""
from __future__ import annotations
from datetime import datetime, timezone
from typing import Any
import hashlib
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
from backend.db import execute, get_connection, get_setting, query_all, query_one

router = APIRouter()

class AlertUpdate(BaseModel):
    status: str

class Simulation(BaseModel):
    ward_id: str
    temperature: float = 36.0
    humidity: float = 35.0
    device_id: str | None = None

@router.get("/alerts")
def alerts(ward_id: str | None = None, risk_class: str | None = Query(default=None, alias="class"), status: str | None = None):
    clauses, params = [], []
    if ward_id:
        clauses.append("a.ward_id=?"); params.append(ward_id)
    class_filter = risk_class
    if class_filter:
        clauses.append("LOWER(a.severity)=LOWER(?)"); params.append(class_filter)
    if status:
        clauses.append("a.status=?"); params.append(status)
    where = " WHERE " + " AND ".join(clauses) if clauses else ""
    rows = query_all("SELECT a.*,w.name AS ward_name FROM alerts a LEFT JOIN wards w ON w.ward_id=a.ward_id" + where + " ORDER BY a.ts DESC", params)
    demo = any(row.get("provenance") == "demo_synthetic" for row in rows)
    return {"alerts":rows,"count":len(rows),"active_count":sum(x.get("status") in ("open","ack") for x in rows),"demo":demo,"provenance":"demo_synthetic" if demo else ("source_configured" if rows else "no_data")}

@router.patch("/alerts/{alert_id}")
def update_alert(alert_id: int, body: AlertUpdate):
    state = body.status.strip().lower()
    if state not in {"ack", "resolved"}:
        raise HTTPException(status_code=422, detail="status must be either 'ack' or 'resolved'.")
    row = query_one("SELECT id FROM alerts WHERE id=?", (alert_id,))
    if not row:
        raise HTTPException(status_code=404, detail=f"Alert {alert_id} was not found.")
    execute("UPDATE alerts SET status=? WHERE id=?", (state,alert_id))
    updated = query_one("SELECT a.*,w.name AS ward_name FROM alerts a LEFT JOIN wards w ON w.ward_id=a.ward_id WHERE a.id=?", (alert_id,))
    demo = bool(updated and updated.get("provenance") == "demo_synthetic")
    return {"alert":updated,"demo":demo,"provenance":"demo_synthetic" if demo else "source_configured"}

def ingest_sensor_reading(*, ward_id: str, device_id: str, temperature: float, humidity: float, quality: str, provenance: str, timestamp: str | None = None) -> dict[str, Any]:
    """Persist one idempotent sensor event and run the same configured alert rules."""
    import math
    ward = query_one("SELECT ward_id,name FROM wards WHERE ward_id=?", (ward_id,))
    if not ward:
        raise ValueError(f"Ward {ward_id!r} was not found.")
    temperature, humidity = float(temperature), float(humidity)
    if not math.isfinite(temperature) or not math.isfinite(humidity) or not 0 <= humidity <= 100:
        raise ValueError("Sensor temperature must be finite and humidity must be between 0 and 100 percent.")
    quality = (quality or "mqtt_unverified").strip()[:80]
    provenance = "demo_synthetic" if provenance == "demo_synthetic" else "source_configured"
    if timestamp:
        try:
            parsed_timestamp = datetime.fromisoformat(str(timestamp).replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError("Sensor timestamp must be ISO-8601.") from exc
        if parsed_timestamp.tzinfo is None:
            parsed_timestamp = parsed_timestamp.replace(tzinfo=timezone.utc)
        ts = parsed_timestamp.astimezone(timezone.utc).isoformat()
    else:
        ts = datetime.now(timezone.utc).isoformat()
    device = (device_id or f"MQTT-{ward_id}").strip()[:160]
    event_key = hashlib.sha256(f"{ward_id}\0{device}\0{ts}".encode("utf-8")).hexdigest()
    duplicate = query_one("SELECT id FROM sensor_readings WHERE event_key=? OR (ward_id=? AND device_id=? AND ts=?) ORDER BY id LIMIT 1", (event_key, ward_id, device, ts))
    if duplicate:
        return {"duplicate": True, "reading_id": duplicate.get("id"), "ward_id": ward_id, "device_id": device, "ts": ts, "quality": quality, "provenance": provenance, "alert_triggered": False, "alert": None, "message": "Duplicate MQTT delivery ignored; the sensor event was already processed."}
    with get_connection() as connection:
        cursor = connection.execute("INSERT INTO sensor_readings(ward_id,device_id,ts,temperature,humidity,quality,provenance,event_key) VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(event_key) DO NOTHING", (ward_id, device, ts, temperature, humidity, quality, provenance, event_key))
        inserted = cursor.rowcount > 0
        reading_id = getattr(cursor, "lastrowid", None)
    if not inserted:
        duplicate = query_one("SELECT id FROM sensor_readings WHERE event_key=?", (event_key,))
        return {"duplicate": True, "reading_id": duplicate.get("id") if duplicate else None, "ward_id": ward_id, "device_id": device, "ts": ts, "quality": quality, "provenance": provenance, "alert_triggered": False, "alert": None, "message": "Duplicate MQTT delivery ignored; another subscriber already stored this sensor event."}
    thresholds = {"temperature_max": 35.0, "high_max": 0.75, **get_setting("thresholds", {})}
    temp_limit = float(thresholds["temperature_max"])
    risk = query_one("SELECT AVG(ahp_risk) AS score FROM edges WHERE ward_id=?", (ward_id,)) or {"score": 0}
    risk_score = float(risk.get("score") or 0)
    temp_breach, risk_breach = temperature >= temp_limit, risk_score >= float(thresholds["high_max"])
    triggered = temp_breach or risk_breach
    new_alert = None
    if triggered:
        severity = "Critical" if temperature >= temp_limit + 3 or risk_breach else "High"
        causes = []
        if temp_breach:
            causes.append(f"sensor temperature {temperature:.1f}°C (threshold {temp_limit:g}°C)")
        if risk_breach:
            causes.append(f"ward mean AHP-inverted risk {risk_score:.2f} (threshold {float(thresholds['high_max']):.2f})")
        alert_id = execute("INSERT INTO alerts(ward_id,ts,severity,cause,precaution,status,provenance) VALUES(?,?,?,?,?,'open',?)", (ward_id, ts, severity, "Threshold breach: " + "; ".join(causes) + ".", "Review the reading and prioritize shade, hydration access, and field verification.", provenance))
        new_alert = query_one("SELECT * FROM alerts WHERE id=?", (alert_id,))
    return {"reading": {"ward_id": ward_id, "device_id": device, "ts": ts, "temperature": temperature, "humidity": humidity, "quality": quality, "provenance": provenance}, "alert_triggered": triggered, "alert": new_alert, "thresholds": thresholds, "demo": provenance == "demo_synthetic", "provenance": provenance, "message": "Synthetic reading stored; no physical sensor was contacted." if provenance == "demo_synthetic" else "MQTT reading stored; physical-device identity has not been independently verified."}


@router.post("/alerts/simulate")
def simulate_reading(body: Simulation):
    try:
        return ingest_sensor_reading(ward_id=body.ward_id, device_id=body.device_id or f"SIM-{body.ward_id}-API", temperature=body.temperature, humidity=body.humidity, quality="simulated", provenance="demo_synthetic")
    except ValueError as exc:
        raise HTTPException(status_code=404 if "was not found" in str(exc) else 422, detail=str(exc)) from exc
