"""Asynchronous, observable execution for GeoCool data and model pipelines.

Jobs are intentionally kept in-process: this is suitable for the single-process demo
service. A durable queue should replace the executor for multi-worker deployments.
"""
from __future__ import annotations

import hashlib
import json
import os
import shlex
import subprocess
import sys
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

ROOT = Path(__file__).resolve().parent
SCRIPTS = ROOT / "scripts"
VALID_JOBS = {"raster", "java", "dataset", "train_and_predict"}
RASTER_NAMES = ("nagpur_lst.tif", "nagpur_ndvi.tif", "nagpur_slope.tif", "nagpur_pop.tif")
_EXECUTOR = ThreadPoolExecutor(max_workers=max(2, int(os.getenv("PIPELINE_WORKERS", "4"))), thread_name_prefix="geocool-job")
_LOCK = threading.RLock()
_JOBS: dict[str, dict[str, Any]] = {}
_ACTIVE_KEYS: dict[tuple[str, str], str] = {}
_IDEMPOTENCY_CACHE: dict[tuple[str, str], str] = {}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def _safe_kwargs(kwargs: dict[str, Any]) -> dict[str, Any]:
    """Return JSON-friendly job arguments without mutating caller input."""
    result: dict[str, Any] = {}
    for key, value in kwargs.items():
        if isinstance(value, (str, int, float, bool)) or value is None:
            result[key] = value
        elif isinstance(value, (list, tuple)):
            result[key] = list(value)
        else:
            result[key] = str(value)
    return result


def _file_fingerprint(path: Path) -> dict[str, Any]:
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


def _log(job: dict[str, Any], stream: str, text: str) -> None:
    line = {"timestamp": _now(), "stream": stream, "message": text.rstrip("\r\n")}
    with _LOCK:
        job["log_lines"].append(line)
        job["updated_at"] = line["timestamp"]


def _record_pipeline_run(job: dict[str, Any]) -> None:
    """Persist a run when the application DB helper is available; never block jobs."""
    try:
        from backend.db import record_pipeline_run  # type: ignore
    except Exception:
        return
    payload = {
        "job": job.get("job_name"),
        "started_at": job.get("started_at") or job.get("created_at"),
        "finished_at": job.get("finished_at"),
        "status": job.get("status"),
        "job_id": job.get("job_id"),
        "log": f"runner_job_id={job.get('job_id')}\n" + "\n".join(f"[{line['timestamp']}] {line['stream']}: {line['message']}" for line in job.get("log_lines", [])),
    }
    try:
        record_pipeline_run(**payload)
    except Exception:
        pass


def _finish(job: dict[str, Any], status: str, *, result: Any = None, error: str | None = None) -> None:
    with _LOCK:
        job["status"] = status
        job["finished_at"] = _now()
        job["updated_at"] = job["finished_at"]
        if result is not None:
            job["result"] = result
        if error:
            job["error"] = error
        key = (job["job_name"], job["idempotency_key"])
        if _ACTIVE_KEYS.get(key) == job["job_id"]:
            _ACTIVE_KEYS.pop(key, None)
    _record_pipeline_run(job)


def _source_checks(job_name: str) -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []
    if job_name == "raster":
        script = SCRIPTS / "1_download_rasters.py"
        checks.append({"name": "Earth Engine downloader", "ok": script.is_file(), "detail": str(script) if script.is_file() else f"Missing {script.name}; add the supplied source script."})
        ee_available = _module_available("ee")
        checks.append({"name": "earthengine-api", "ok": ee_available, "detail": "Python package is importable." if ee_available else "Not installed; install earthengine-api before downloading."})
        geemap_available = _module_available("geemap")
        checks.append({"name": "geemap", "ok": geemap_available, "detail": "Python package is importable." if geemap_available else "Not installed; install geemap before downloading."})
        auth = _earth_engine_auth_evidence()
        checks.append({"name": "Earth Engine credentials", "ok": auth[0], "detail": auth[1]})
        checks.append({"name": "Transfer fallback", "ok": True, "detail": "Direct export is followed once by bounded Earth Engine tiles and an atomic rasterio GeoTIFF mosaic if any source output is missing or invalid."})
        rasterio_available = _module_available("rasterio")
        checks.append({"name": "Raster mosaic dependency", "ok": rasterio_available, "detail": "rasterio is available for the tile mosaic." if rasterio_available else "Install rasterio from requirements-pipeline.txt to merge bounded export tiles."})
        chunk_script = SCRIPTS / "download_rasters_chunked.py"
        checks.append({"name": "Chunked transfer fallback", "ok": chunk_script.is_file(), "detail": "Verified tile download and GeoTIFF mosaic fallback is available." if chunk_script.is_file() else "Add the tile-export fallback script before launching large raster exports."})
    elif job_name == "java":
        path = SCRIPTS / "GisImageDownloader.java"
        checks.append({"name": "Legacy Java downloader", "ok": path.is_file(), "detail": "Warning: legacy downloader targets its hard-coded /nagpur/5km_radius/ S3 area, not the canonical ROI." if path.is_file() else f"Missing {path.name}."})
        javac = _which("javac")
        java = _which("java")
        checks.append({"name": "Java compiler/runtime", "ok": bool(javac and java), "detail": "javac and java found." if javac and java else "Install a JDK (javac and java) to use the fallback."})
    elif job_name == "dataset":
        script = SCRIPTS / "2_build_real_dataset.py"
        checks.append({"name": "Dataset builder", "ok": script.is_file(), "detail": str(script) if script.is_file() else f"Missing {script.name}; add the supplied source script."})
        raster_paths = [ROOT / "data" / "raw" / name for name in RASTER_NAMES]
        missing = [path.name for path in raster_paths if not _valid_tiff(path)]
        checks.append({"name": "Raster inputs", "ok": not missing, "detail": "All four non-empty GeoTIFF inputs are present." if not missing else f"Missing/empty GeoTIFF input(s): {', '.join(missing)}. Run the Earth Engine or Java raster job first; the original builder otherwise substitutes zeros."})
        gis_modules = ("osmnx", "geopandas", "pandas", "rasterstats")
        missing_modules = [module for module in gis_modules if not _module_available(module)]
        checks.append({"name": "GIS dependencies", "ok": not missing_modules, "detail": "All source-builder packages are importable." if not missing_modules else f"Install pipeline extras; missing: {', '.join(missing_modules)}."})
        checks.append({"name": "Geometry wrapper", "ok": (SCRIPTS / "build_dataset_patched.py").is_file(), "detail": "Geometry-preserving wrapper is available." if (SCRIPTS / "build_dataset_patched.py").is_file() else "Wrapper missing."})
    elif job_name == "train_and_predict":
        checks.append({"name": "ML dependencies", "ok": _module_available("sklearn"), "detail": "scikit-learn available." if _module_available("sklearn") else "Install scikit-learn to train models."})
        data_candidates = [ROOT / "data" / "processed" / "edge_suitability.csv", ROOT / "data" / "demo_data.json"]
        exists = any(p.is_file() for p in data_candidates)
        checks.append({"name": "Training data", "ok": exists, "detail": "A demo JSON or processed CSV exists." if exists else "No input found; run seed_demo.py or build the real dataset first."})
    return checks


def _module_available(module: str) -> bool:
    try:
        __import__(module)
        return True
    except Exception:
        return False


def _which(command: str) -> str | None:
    from shutil import which
    return which(command)


def _earth_engine_auth_evidence() -> tuple[bool, str]:
    """Check for credential evidence only; this does not authenticate with EE."""
    paths = []
    if os.getenv("GOOGLE_APPLICATION_CREDENTIALS"):
        paths.append(Path(os.environ["GOOGLE_APPLICATION_CREDENTIALS"]))
    if os.getenv("EARTHENGINE_TOKEN_PATH"):
        paths.append(Path(os.environ["EARTHENGINE_TOKEN_PATH"]))
    paths.extend([Path.home() / ".config" / "earthengine" / "credentials", Path.home() / ".config" / "gcloud" / "application_default_credentials.json"])
    found = next((path for path in paths if path.is_file() and os.access(path, os.R_OK)), None)
    if found:
        return True, f"Readable credential file found at {found}; validity is not verified until the Earth Engine script runs."
    return False, "No readable Earth Engine/Google credential file detected. Run `earthengine authenticate` or configure a service-account credential file; preflight cannot verify token validity."


def preflight(job_name: str) -> dict[str, Any]:
    """Return readiness checks without launching the requested pipeline."""
    if job_name not in VALID_JOBS:
        return {"job_name": job_name, "ok": False, "checks": [], "error": f"Unknown job. Choose one of: {', '.join(sorted(VALID_JOBS))}."}
    checks = _source_checks(job_name)
    return {"job_name": job_name, "ok": all(check["ok"] for check in checks), "checks": checks, "checked_at": _now()}


def _friendly_error(job_name: str, text: str) -> str:
    lower = text.lower()
    if any(token in lower for token in ("not authenticated", "authentication required", "could not authenticate", "authorization required", "eeexception: user", "credentials not found", "user credentials are not set", "please authorize access", "earth engine authenticate", "permission_denied", "invalid credentials")):
        return "Earth Engine authentication failed or is unavailable. Run `earthengine authenticate` (or configure a readable service-account key and project), then retry. See the timestamped job log for context."
    if "no module named 'ee'" in lower or 'no module named "ee"' in lower or "no module named 'geemap'" in lower:
        return "The Earth Engine downloader dependencies are missing. Install earthengine-api and geemap (for example, `pip install -r requirements-pipeline.txt`) and retry."
    if any(token in lower for token in ("50 mb", "50mb", "32 mb", "32mb", "response too large", "file too large", "download limit", "content-length")):
        return "Raster transfer exceeded a direct-download/response size limit (commonly 50 MB or 32 MB). The preserved source downloader does not implement Drive/tile retries; use Earth Engine Drive exports or smaller tiles and verify the four GeoTIFFs before rebuilding. No download is reported successful without output evidence."
    if job_name == "java":
        return "The legacy Java fallback failed. It targets a hard-coded 5 km Nagpur S3 path rather than the canonical ROI; check the log, JDK, network, and source script before retrying."
    return "Job failed. Review the timestamped stdout/stderr log, verify the source inputs and optional dependencies, and retry after correcting the reported issue."


def _run_subprocess(job: dict[str, Any], command: list[str], *, cwd: Path = ROOT, env: dict[str, str] | None = None) -> dict[str, Any]:
    _log(job, "system", f"Starting: {shlex.join(command)}")
    merged = os.environ.copy()
    if env:
        merged.update({str(k): str(v) for k, v in env.items() if v is not None})
    proc = subprocess.Popen(command, cwd=str(cwd), env=merged, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, bufsize=1, errors="replace")
    job["process_id"] = proc.pid
    threads: list[threading.Thread] = []
    def forward(pipe: Any, stream: str) -> None:
        try:
            for line in iter(pipe.readline, ""):
                _log(job, stream, line)
        finally:
            pipe.close()
    for pipe, name in ((proc.stdout, "stdout"), (proc.stderr, "stderr")):
        thread = threading.Thread(target=forward, args=(pipe, name), daemon=True)
        thread.start()
        threads.append(thread)
    timeout = float(job["kwargs"].get("timeout_seconds", os.getenv("PIPELINE_JOB_TIMEOUT_SECONDS", "7200")))
    try:
        code = proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.kill()
        code = proc.wait()
        _log(job, "error", f"Subprocess exceeded the configured {timeout:g}-second timeout and was terminated.")
        for thread in threads:
            thread.join()
        raise TimeoutError(f"Subprocess timed out after {timeout:g} seconds; inspect the streamed log before retrying.")
    for thread in threads:
        thread.join()
    return {"returncode": code, "command": command}


def _dispatch(job: dict[str, Any]) -> Any:
    name, args = job["job_name"], job["kwargs"]
    readiness = preflight(name)
    blocked = [check for check in readiness.get("checks", []) if not check.get("ok")]
    if blocked:
        for check in blocked:
            _log(job, "preflight", f"{check.get('name')}: {check.get('detail')}")
        raise RuntimeError("Preflight blocked this job: " + " | ".join(f"{check.get('name')}: {check.get('detail')}" for check in blocked))
    raster_before: dict[str, tuple[int, int] | None] | None = None
    if name == "raster":
        script = SCRIPTS / "1_download_rasters.py"
        if not script.is_file():
            raise RuntimeError(f"Required source script is missing: {script}")
        mode = str(args.get("export_mode") or os.getenv("GEE_EXPORT_MODE", "direct"))
        env = {"GEE_EXPORT_MODE": mode, "GEE_TILE_SIZE": args.get("tile_size", os.getenv("GEE_TILE_SIZE", "")), "GEE_DRIVE_FOLDER": args.get("drive_folder", os.getenv("GEE_DRIVE_FOLDER", ""))}
        _log(job, "system", f"Earth Engine export mode requested: {mode}. Canonical ROI belongs to the original Python script. The preserved source downloader currently does not consume GEE_EXPORT_MODE/GEE_TILE_SIZE/GEE_DRIVE_FOLDER; these settings are advisory and do not themselves implement a retry.")
        raster_before = _raster_signatures()
        outcome = _run_subprocess(job, [sys.executable, str(script)], env=env)
        missing_after_direct = _unverified_rasters(raster_before)
        if outcome["returncode"] != 0 and not missing_after_direct:
            _log(job, "warning", "The preserved downloader returned a non-zero exit code after all four new GeoTIFFs passed file-signature verification; accepting only the verified outputs.")
            outcome = {**outcome, "returncode": 0}
        elif missing_after_direct:
            tail = "\n".join(item["message"] for item in job["log_lines"][-30:]).lower()
            if any(token in tail for token in ("not authenticated", "authentication required", "could not authenticate", "credentials not found", "user credentials are not set", "permission_denied", "invalid credentials")):
                raise RuntimeError(_friendly_error(name, tail))
            _log(job, "warning", f"Direct Earth Engine export left unverified outputs: {', '.join(missing_after_direct)}. Retrying once with bounded ROI tiles and an atomic GeoTIFF mosaic.")
            chunked = SCRIPTS / "download_rasters_chunked.py"
            outcome = _run_subprocess(job, [sys.executable, str(chunked)], env={"GEE_TILE_SPLITS": args.get("tile_splits", os.getenv("GEE_TILE_SPLITS", "4"))})
    elif name == "java":
        source = SCRIPTS / "GisImageDownloader.java"
        if not source.is_file():
            raise RuntimeError(f"Required source file is missing: {source}")
        javac, java = _which("javac"), _which("java")
        if not javac or not java:
            raise RuntimeError("A JDK is required (both javac and java must be installed).")
        _log(job, "warning", "LEGACY FALLBACK: Java targets /nagpur/5km_radius/ and is not the canonical Python ROI [78.89, 20.96, 79.29, 21.33].")
        (ROOT / "models").mkdir(parents=True, exist_ok=True)
        compile_result = _run_subprocess(job, [javac, "-d", str(ROOT / "models"), str(source)])
        if compile_result["returncode"]:
            raise RuntimeError("Java compilation failed; inspect stderr in the job log.")
        raster_before = _raster_signatures()
        outcome = _run_subprocess(job, [java, "-cp", str(ROOT / "models"), "GisImageDownloader"])
    elif name == "dataset":
        wrapper = SCRIPTS / "build_dataset_patched.py"
        if not wrapper.is_file():
            raise RuntimeError(f"Required wrapper is missing: {wrapper}")
        missing_rasters = [name for name in RASTER_NAMES if not _valid_tiff(ROOT / "data" / "raw" / name)]
        if missing_rasters:
            raise RuntimeError(f"Dataset build needs four verified raster inputs; missing/empty: {', '.join(missing_rasters)}. Run the raster pipeline first. The original builder otherwise silently substitutes zeros.")
        cmd = [sys.executable, str(wrapper)]
        outcome = _run_subprocess(job, cmd, env={"GECOOL_ROOT": str(ROOT)})
    elif name == "train_and_predict":
        from backend.routes.model import PredictRequest, _call_train, predict
        train_opts: dict[str, Any] = {}
        for key in ("split_ratio", "folds", "seed"):
            if key in args:
                train_opts[key] = args[key]
        training = _call_train(train_opts)
        if training.get("unavailable_reason") or not training.get("models"):
            raise RuntimeError(f"Training did not produce a usable model: {training.get('unavailable_reason') or 'no model artifacts were saved'}")
        _log(job, "stdout", f"Training completed: {len(training.get('metrics', []))} model metric record(s); provenance={training.get('provenance', 'unknown')}.")
        prediction_result = predict(PredictRequest())
        predictions = prediction_result.get("predictions", [])
        if not predictions:
            raise RuntimeError("Training finished but no ward-level prediction was persisted; verify the data mapping and saved model artifacts.")
        _log(job, "stdout", f"Prediction completed and persisted: {prediction_result.get('persisted', 0)} ward-level result(s).")
        return {"training": training, "prediction_result": prediction_result}
    else:
        raise RuntimeError(f"Unsupported job {name!r}.")
    if outcome["returncode"] != 0:
        tail = "\n".join(item["message"] for item in job["log_lines"][-40:])
        raise RuntimeError(_friendly_error(name, tail))
    if name in {"raster", "java"} and raster_before is not None:
        missing = _unverified_rasters(raster_before)
        if missing:
            raise RuntimeError(f"Downloader exited with code 0, but these expected GeoTIFFs were not newly verified as valid outputs: {', '.join(missing)}. Check per-file stderr and credentials; no download success is claimed.")
    return outcome


def _raster_signatures() -> dict[str, tuple[int, int] | None]:
    root = ROOT / "data" / "raw"
    signatures: dict[str, tuple[int, int] | None] = {}
    for name in RASTER_NAMES:
        path = root / name
        try:
            stat = path.stat()
            signatures[name] = (stat.st_mtime_ns, stat.st_size)
        except OSError:
            signatures[name] = None
    return signatures


def _valid_tiff(path: Path) -> bool:
    try:
        if path.stat().st_size <= 4:
            return False
        with path.open("rb") as handle:
            return handle.read(4) in (b"II*\x00", b"MM\x00*")
    except OSError:
        return False


def _unverified_rasters(before: dict[str, tuple[int, int] | None]) -> list[str]:
    root = ROOT / "data" / "raw"
    missing: list[str] = []
    for name in RASTER_NAMES:
        path = root / name
        try:
            stat = path.stat()
            signature = (stat.st_mtime_ns, stat.st_size)
            with path.open("rb") as handle:
                header = handle.read(4)
        except OSError:
            missing.append(name)
            continue
        is_tiff = header in (b"II*\x00", b"MM\x00*")
        if stat.st_size <= 4 or not is_tiff or before.get(name) == signature:
            missing.append(name)
    return missing


def _worker(job_id: str) -> None:
    with _LOCK:
        job = _JOBS[job_id]
        job["status"] = "running"
        job["started_at"] = _now()
        job["updated_at"] = job["started_at"]
    _log(job, "system", f"Job {job['job_name']} started.")
    _record_pipeline_run(job)
    try:
        result = _dispatch(job)
    except Exception as exc:
        message = str(exc).strip() or exc.__class__.__name__
        _log(job, "error", message)
        _finish(job, "failed", error=message)
    else:
        _log(job, "system", "Job completed successfully.")
        _finish(job, "success", result=result)


def launch_job(job_name: str, **kwargs: Any) -> dict[str, Any]:
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


def get_job(job_id: str) -> dict[str, Any] | None:
    with _LOCK:
        job = _JOBS.get(job_id)
        return dict(job) if job is not None else None


def list_jobs() -> list[dict[str, Any]]:
    with _LOCK:
        return [dict(_JOBS[key]) for key in sorted(_JOBS, key=lambda item: _JOBS[item]["created_at"], reverse=True)]


def iter_job_events(job_id: str, after: int = 0) -> Iterator[dict[str, Any]]:
    """Yield buffered lines as they arrive; finish after a terminal job state."""
    def generate() -> Iterator[dict[str, Any]]:
        cursor = max(0, int(after))
        while True:
            job = get_job(job_id)
            if job is None:
                return
            lines = job.get("log_lines", [])
            while cursor < len(lines):
                yield lines[cursor]
                cursor += 1
            if job.get("status") in {"success", "failed"} and cursor >= len(lines):
                return
            threading.Event().wait(0.2)
    return generate()


def start_raster_pipeline(**kwargs: Any) -> dict[str, Any]:
    """Compatibility alias for the API router."""
    return launch_job("raster", **kwargs)


def start_java_pipeline(**kwargs: Any) -> dict[str, Any]:
    """Compatibility alias for the API router."""
    return launch_job("java", **kwargs)


def start_dataset_pipeline(**kwargs: Any) -> dict[str, Any]:
    """Compatibility alias for the API router."""
    return launch_job("dataset", **kwargs)


def stream_events(job_id: str) -> Iterator[dict[str, Any]]:
    """Compatibility name used by the API's Server-Sent Events endpoint."""
    return iter_job_events(job_id)


def run_train_and_predict(**kwargs: Any) -> dict[str, Any]:
    """Synchronous convenience API using the same training/prediction routines."""
    from backend.ml.train import train_models
    from backend.ml.predict import predict_all
    training = train_models(**{k: kwargs[k] for k in ("split_ratio", "folds", "seed") if k in kwargs})
    return {"training": training, "predictions": predict_all()}
