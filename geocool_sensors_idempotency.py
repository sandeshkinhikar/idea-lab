from pathlib import Path
import re
root=Path('/home/ubuntu/geocool')

p=root/'backend/routes/alerts.py'; s=p.read_text()
# Replace the simulation-only endpoint body with a shared ingestion path.
start=s.index('@router.post("/alerts/simulate")')
new=r'''def ingest_sensor_reading(*, ward_id: str, device_id: str, temperature: float, humidity: float, quality: str, provenance: str, timestamp: str | None = None) -> dict[str, Any]:
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
    ts = timestamp or datetime.now(timezone.utc).isoformat()
    device = (device_id or f"MQTT-{ward_id}").strip()[:160]
    duplicate = query_one("SELECT id FROM sensor_readings WHERE ward_id=? AND device_id=? AND ts=?", (ward_id, device, ts))
    if duplicate:
        return {"duplicate": True, "reading_id": duplicate.get("id"), "ward_id": ward_id, "device_id": device, "ts": ts, "quality": quality, "provenance": provenance, "alert_triggered": False, "alert": None, "message": "Duplicate MQTT delivery ignored; the sensor event was already processed."}
    execute("INSERT INTO sensor_readings(ward_id,device_id,ts,temperature,humidity,quality,provenance) VALUES(?,?,?,?,?,?,?)", (ward_id, device, ts, temperature, humidity, quality, provenance))
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
'''
s=s[:start]+new+'\n'
p.write_text(s)

p=root/'backend/mqtt_simulator.py'; s=p.read_text()
anchor='''def _new_client(mqtt: Any, client_id: str) -> Any:
'''
helper=r'''def ingest_mqtt_message(topic: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Validate a subscribed topic/payload and persist it through the alert pipeline."""
    parts = str(topic).split("/")
    if len(parts) != 4 or parts[0] != "nagpur" or parts[1] != "wards" or parts[3] != "sensors" or not parts[2] or parts[2] in {"+", "#"}:
        raise ValueError("Expected topic nagpur/wards/{ward_id}/sensors.")
    ward_id = parts[2]
    payload_ward = str(payload.get("ward_id", ward_id))
    if payload_ward != ward_id:
        raise ValueError("MQTT topic ward_id and payload ward_id do not match.")
    quality = str(payload.get("quality") or "mqtt_unverified")
    source = str(payload.get("provenance") or "")
    simulated = bool(payload.get("simulated")) or quality.lower() in {"simulated", "demo"} or any(tag in source.lower() for tag in ("demo", "synthetic", "simulat"))
    timestamp = payload.get("timestamp") or payload.get("ts")
    if timestamp:
        try:
            datetime.fromisoformat(str(timestamp).replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError("MQTT timestamp must be ISO-8601.") from exc
    from backend.routes.alerts import ingest_sensor_reading
    return ingest_sensor_reading(
        ward_id=ward_id,
        device_id=str(payload.get("device_id") or f"MQTT-{ward_id}"),
        temperature=float(payload.get("temperature")),
        humidity=float(payload.get("humidity")),
        quality="simulated" if simulated else quality,
        provenance="demo_synthetic" if simulated else "source_configured",
        timestamp=str(timestamp) if timestamp else None,
    )


'''
if s.count(anchor)!=1: raise SystemExit('mqtt client helper anchor mismatch')
s=s.replace(anchor,helper+anchor,1)
old='''    def on_message(client_obj: Any, userdata: Any, message: Any) -> None:
        try:
            payload = json.loads(message.payload.decode("utf-8"))
        except Exception:
            payload = {"raw": message.payload.decode("utf-8", errors="replace")}
        print(json.dumps({"topic": message.topic, "payload": payload, "received_at": datetime.now(timezone.utc).isoformat(), "physical_node_verified": False}), flush=True)
'''
new='''    def on_message(client_obj: Any, userdata: Any, message: Any) -> None:
        try:
            payload = json.loads(message.payload.decode("utf-8"))
            if not isinstance(payload, dict):
                raise ValueError("MQTT payload must be a JSON object.")
            result = ingest_mqtt_message(message.topic, payload)
            print(json.dumps({"topic": message.topic, "result": result, "received_at": datetime.now(timezone.utc).isoformat(), "physical_node_verified": False}), flush=True)
        except Exception as exc:
            print(json.dumps({"topic": message.topic, "error": str(exc), "received_at": datetime.now(timezone.utc).isoformat()}), file=sys.stderr, flush=True)
'''
if s.count(old)!=1: raise SystemExit('mqtt callback anchor mismatch')
p.write_text(s.replace(old,new,1))

p=root/'backend/pipeline_runner.py'; s=p.read_text()
s=s.replace('import os\nimport shlex', 'import hashlib\nimport json\nimport os\nimport shlex',1)
anchor='''def _log(job: dict[str, Any], stream: str, text: str) -> None:
'''
helper=r'''def _file_fingerprint(path: Path) -> dict[str, Any]:
    try:
        stat = path.stat()
        return {"path": str(path.relative_to(ROOT)), "size": stat.st_size, "mtime_ns": stat.st_mtime_ns}
    except OSError:
        return {"path": str(path.relative_to(ROOT)), "missing": True}


def _default_job_key(job_name: str, kwargs: dict[str, Any]) -> str:
    sources: list[Path] = []
    if job_name == "raster":
        sources = [SCRIPTS / "1_download_rasters.py", SCRIPTS / "download_rasters_chunked.py"]
        try:
            auth_ready = bool(_earth_engine_auth_evidence()[0])
        except Exception:
            auth_ready = False
    elif job_name == "java":
        sources = [SCRIPTS / "GisImageDownloader.java"]
        auth_ready = None
    elif job_name == "dataset":
        sources = [SCRIPTS / "2_build_real_dataset.py", SCRIPTS / "build_dataset_patched.py", *(ROOT / "data" / "raw" / name for name in RASTER_NAMES)]
        auth_ready = None
    else:
        sources = [ROOT / "data" / "processed" / "edge_suitability.csv", ROOT / "data" / "demo_data.json"]
        auth_ready = None
    try:
        if job_name == "train_and_predict":
            from backend.db import get_setting
            threshold_settings = get_setting("thresholds", {})
        else:
            threshold_settings = None
    except Exception:
        threshold_settings = None
    payload = {"job": job_name, "options": kwargs, "inputs": [_file_fingerprint(path) for path in sources], "auth_ready": auth_ready, "thresholds": threshold_settings}
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    return "auto:" + hashlib.sha256(encoded).hexdigest()


'''
if s.count(anchor)!=1: raise SystemExit('pipeline key helper anchor mismatch')
s=s.replace(anchor,helper+anchor,1)
start=s.index('def launch_job(job_name: str, **kwargs: Any) -> dict[str, Any]:')
end=s.index('\n\ndef get_job(',start)
new=r'''def launch_job(job_name: str, **kwargs: Any) -> dict[str, Any]:
    """Queue a job; equivalent calls coalesce and reuse prior successful results."""
    if job_name not in VALID_JOBS:
        raise ValueError(f"Unknown job {job_name!r}; expected one of {', '.join(sorted(VALID_JOBS))}.")
    safe_kwargs = _safe_kwargs(kwargs)
    supplied_key = safe_kwargs.pop("idempotency_key", None)
    force_new = bool(safe_kwargs.pop("force", False))
    if supplied_key is not None:
        identity = f"explicit:{supplied_key}"
    elif force_new:
        identity = f"forced:{uuid.uuid4()}"
    else:
        identity = _default_job_key(job_name, safe_kwargs)
    key = (job_name, identity)
    with _LOCK:
        cached_id = _IDEMPOTENCY_CACHE.get(key)
        if cached_id and cached_id in _JOBS:
            cached = _JOBS[cached_id]
            if cached.get("status") in {"queued", "running", "success"}:
                return {**cached, "idempotent_reuse": True}
        active_id = _ACTIVE_KEYS.get(key)
        if active_id and _JOBS.get(active_id, {}).get("status") in {"queued", "running"}:
            return {**_JOBS[active_id], "idempotent_reuse": True}
        job_id = str(uuid.uuid4())
        job = {
            "job_id": job_id, "job_name": job_name, "status": "queued", "created_at": _now(),
            "updated_at": _now(), "started_at": None, "finished_at": None, "kwargs": safe_kwargs,
            "log_lines": [], "idempotency_key": identity, "idempotent_reuse": False,
            "preflight": preflight(job_name),
        }
        _JOBS[job_id] = job
        _ACTIVE_KEYS[key] = job_id
        _IDEMPOTENCY_CACHE[key] = job_id
        _log(job, "system", "Job queued.")
        _record_pipeline_run(job)
        _EXECUTOR.submit(_worker, job_id)
        return dict(job)
'''
s=s[:start]+new+s[end:]
p.write_text(s)
print('Added shared sensor-to-alert ingestion and default job fingerprint/reuse logic.')