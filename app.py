"""GeoCool FastAPI application and static frontend host."""
from __future__ import annotations

import os
import time
from pathlib import Path
from urllib.parse import quote
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

try:
    from .db import ensure_demo_seed, init_db, database_health
    from .routes.alerts import router as alerts_router
    from .routes.metrics import router as metrics_router
    from .routes.model import router as model_router
    from .routes.pipeline import router as pipeline_router
    from .routes.settings import router as settings_router
    from .routes.wards import router as wards_router
    from .pipeline_runner import _earth_engine_auth_evidence
except ImportError:
    from db import ensure_demo_seed, init_db, database_health
    from alerts import router as alerts_router
    from metrics import router as metrics_router
    from model import router as model_router
    from pipeline import router as pipeline_router
    from settings import router as settings_router
    from wards import router as wards_router
    try:
        from pipeline_runner import _earth_engine_auth_evidence
    except ImportError:  # pragma: no cover - fallback for a missing backend package wrapper.
        def _earth_engine_auth_evidence():
            return False, "Earth Engine auth is unavailable in this environment."

ROOT = Path(__file__).resolve().parent
FRONTEND = ROOT / "frontend" if (ROOT / "frontend").exists() else ROOT
ASSET_ROOT = ROOT / "assets" if (ROOT / "assets").exists() else ROOT
app = FastAPI(title="GeoCool Nagpur Climate Risk", version="0.1.0", description="Explainable demo prototype; all seeded data is marked DEMO DATA.")

@app.middleware("http")
async def add_process_time(request: Request, call_next):
    start = time.perf_counter()
    response = await call_next(request)
    response.headers["X-Process-Time-Ms"] = f"{(time.perf_counter()-start)*1000:.2f}"
    return response

@app.on_event("startup")
def startup() -> None:
    init_db()
    if os.getenv("DEMO_MODE", "true").lower() in {"1", "true", "yes"}:
        ensure_demo_seed()

@app.get("/health")
def health():
    started = time.perf_counter()
    status = database_health()
    raster_names = ("nagpur_lst.tif", "nagpur_ndvi.tif", "nagpur_slope.tif", "nagpur_pop.tif")
    raster_files = []
    for name in raster_names:
        path = ROOT / "data" / "raw" / name
        raster_files.append({"name": name, "present": path.is_file() and path.stat().st_size > 4, "size_bytes": path.stat().st_size if path.is_file() else None})
    model_paths = sorted(path.name for path in (ROOT / "models").glob("*.joblib")) if (ROOT / "models").exists() else []
    try:
        try:
            from .pipeline_runner import _earth_engine_auth_evidence
        except ImportError:
            from pipeline_runner import _earth_engine_auth_evidence
        auth_configured, _ = _earth_engine_auth_evidence()
    except Exception:
        auth_configured = False
    latency_ms = round((time.perf_counter() - started) * 1000, 2)
    return {
        "ok": bool(status.get("connected")),
        "database": status,
        "rasters": {"files": raster_files, "present_count": sum(1 for item in raster_files if item["present"]), "total": len(raster_files)},
        "model_files": {"files": model_paths, "count": len(model_paths)},
        "earth_engine_auth": {"configured": auth_configured, "status": "credential file detected; validity is checked when the pipeline runs" if auth_configured else "not configured; authenticate Earth Engine before starting downloads"},
        "earth_engine": "configured (credential evidence detected)" if auth_configured else "not configured",
        "latency_ms": latency_ms,
        "demo_mode": os.getenv("DEMO_MODE", "true").lower() in {"1", "true", "yes"},
    }

@app.get("/api/health")
def api_health():
    return health()

@app.get("/api/map-config")
def map_config():
    """Return a public browser basemap URL; never include unrelated credentials."""
    key = os.getenv("CARTO_API_KEY", "").strip()
    if key:
        return {
            "provider": "carto",
            "label": "CartoDB Positron",
            "tile_url": f"https://{{s}}.basemaps.cartocdn.com/light_all/{{z}}/{{x}}/{{y}}{{r}}.png?key={quote(key, safe='')}",
            "subdomains": "abcd",
            "attribution": '&copy; <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noopener">OpenStreetMap contributors</a> &copy; <a href="https://carto.com/attribution/" target="_blank" rel="noopener">CARTO</a>',
            "fallback": False,
        }
    return {
        "provider": "osm",
        "label": "OpenStreetMap · CARTO key not configured",
        "tile_url": "https://tile.openstreetmap.org/{z}/{x}/{y}.png",
        "subdomains": None,
        "attribution": '&copy; <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noopener">OpenStreetMap contributors</a>',
        "fallback": True,
    }

for route in (wards_router, alerts_router, metrics_router, model_router, settings_router, pipeline_router):
    app.include_router(route, prefix="/api")

if ASSET_ROOT.exists():
    app.mount("/assets", StaticFiles(directory=ASSET_ROOT), name="assets")

@app.get("/manus-routes.json", include_in_schema=False)
def route_manifest():
    return FileResponse(ROOT / "manus-routes.json", media_type="application/json")

@app.get("/{page_name}.html", include_in_schema=False)
def page_alias(page_name: str):
    known = {"index","dashboard","ward","alerts","metrics","data","settings","how"}
    if page_name not in known:
        return JSONResponse({"detail":"Not found"},status_code=404)
    shell = FRONTEND / f"{page_name}.html"
    if shell.is_file():
        return FileResponse(shell, media_type="text/html")
    return FileResponse(FRONTEND / "index.html")

@app.get("/{full_path:path}", include_in_schema=False)
def spa_fallback(full_path: str):
    if full_path.startswith("api/"):
        return JSONResponse({"detail":"Not found"},status_code=404)
    candidate = FRONTEND / full_path
    if full_path and candidate.is_file():
        return FileResponse(candidate)
    return FileResponse(FRONTEND / "index.html")
