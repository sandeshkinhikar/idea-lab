# GeoCool Nagpur Climate Risk — Implementation Plan

## Product and implementation approach

Build the user’s report-driven urban climate-risk prototype as a FastAPI + Uvicorn service that serves a plain HTML/CSS/JavaScript application without a frontend build. Preserve the three source scripts byte-for-byte in `scripts/`; all integration belongs in additive wrappers, the runner, or backend modules. The same FastAPI process owns the browser routes, same-origin JSON API, health checks, pipeline event stream, and static frontend.

Leaflet renders Ward and Street views over CartoDB Positron when a public, referrer-restricted `CARTO_API_KEY` is configured. CARTO's raster service now requires that key. A keyless Preview uses visibly labeled OpenStreetMap tiles with proper attribution rather than hiding CARTO's notice. Chart.js renders trends and feature contributions. PostgreSQL/PostGIS is the requested spatial database and runs in `docker-compose.yml`; `backend/db.py` supports that backend and a local SQLite compatibility database so the initialized Preview can run the deterministic demo without a separately managed database. `seed_demo.py` generates only explicitly synthetic records. The UI and APIs carry provenance; turning off a Demo Mode preference never relabels seeded records as real.

The report’s Python ROI `[78.89, 20.96, 79.29, 21.33]` and OSMnx `dist=20000` are canonical. The Java source’s `/nagpur/5km_radius/` and its exact S3 URL remain a warning-labeled legacy fallback. An additive wrapper appends geometry while preserving the original builder’s five feature values. Earth Engine downloads use the original direct export first, with a bounded tile-export/mosaic path for large images and actionable credential/size failures. Four asynchronous jobs—raster download, Java fallback, dataset build, and train/predict—have preflight checks, IDs, status transitions, persistent run logs, and SSE output.

Street edges remain the source granularity. PostGIS `ST_Intersects` derives ward summaries, including mean, maximum and p90. The exact AHP formula remains `(1−norm(lst))×0.35 + norm(ndvi)×0.25 + (1−norm(pm))×0.20 + (1−norm(population))×0.10 + (1−norm(slope))×0.10`. That value is suitability (higher is cooler/better); risk is `1 − composite_suitability_score`. PM remains the constant `65.0` placeholder and is not represented as a measured input.

The ML layer trains a LinearRegression baseline, RandomForest classifier/regressor and XGBoost classifier/regressor; joblib artifacts live in `models/`, and each run’s metrics and validation detail are persisted in `model_runs`. Labels are configurable weak supervision derived from AHP-inverted risk—not field ground truth. Real CSV records are never trained edge-by-edge: `geometry_wkt` is assigned through batched PostGIS `ST_Intersects` to each intersecting official non-demo ward. If such boundaries are absent or geometry cannot be matched, the training endpoint returns an actionable unavailable/error result and never invents a ward ID or treats demo polygons as real. Holdout uses geometry-derived spatial blocks when available or a temporal split if source timestamps exist; group-aware/stratified k-fold is used when sample counts permit. SHAP TreeExplainer is preferred when installed; documented permutation importance is the fallback. All scores show source, caveat and a plain-language explanation.

## Project structure and responsibilities

- `backend/app.py`: FastAPI bootstrap, startup database initialization, JSON/health routes, static hosting and client-facing route fallback.
- `backend/routes/{wards,alerts,metrics,model,settings,pipeline}.py`: bounded data endpoints, score aggregation, model/alert settings and controls, pipeline launch/status/SSE, and CSV/PDF exports.
- `backend/db.py` and `backend/schema.sql`: SQLite demo/PostgreSQL adapter and PostGIS schema, migrations, seed, geometry and persistence helpers.
- `backend/ml/{train,explain,predict}.py`: allowlisted data loading, weak-label model training/evaluation, artifacts, feature contributions and current predictions.
- `backend/pipeline_runner.py` and `backend/mqtt_simulator.py`: preflighted, input-fingerprinted jobs that coalesce repeated equivalent runs (with explicit `force` support), timestamped logs, a simulated publisher, and an MQTT subscriber that persists readings and runs shared alert thresholds. A database-unique event key makes duplicate `ward_id/device_id/timestamp` delivery idempotent across concurrent subscribers; physical-device identity is not asserted.
- `frontend/index.html` and route shells: map-first application shell; URL routes are `/`, `/alerts`, `/metrics`, `/data`, `/settings`, `/how`, and `/ward/:ward_id`.
- `frontend/assets/css/theme.css`: supplied color tokens, readable light/dark surfaces, contrast-safe controls, map/dashboard layout, risk palette, focus and reduced-motion styles.
- `frontend/assets/js/{api,map,charts,ui,contrast-audit}.js`: same-origin request/error/download helpers, Leaflet, Chart.js, page rendering/navigation, all actions, and a developer-only text-plus-focus-indicator contrast audit.
- `scripts/`: the user’s original Earth Engine downloader, OSMnx/AHP dataset builder and Java S3 fallback, plus the geometry-preserving wrapper and additive bounded raster tiler.
- `data/raw/`, `data/processed/`, `models/`: four source rasters, the allowlisted edge CSV and generated model artifacts; absent real rasters are reported rather than faked.
- Root configuration/docs: `docker-compose.yml`, `Dockerfile`, `.env.example`, `seed_demo.py`, `manus-routes.json`, `BUTTONS.md`, `RECONCILIATION.md`, `LIMITATIONS.md`, `METHODOLOGY.md`, `README.md`, `TODO.md` and project metadata.

## Design system

**Design movement:** civic geospatial operations console with Swiss-cartographic clarity and the restrained hierarchy of a municipal command room.

**Core principles:** (1) map first, with detail and actions adjacent to the geography; (2) every value carries a direction, unit, provenance and explanation; (3) restrained civic typography and no decorative dashboard clutter; (4) errors and weak-data caveats are legible before actions are taken.

**Color philosophy:** use the specified navy and blue as trustworthy civic navigation/action colors, slate text for high-contrast reading, and the four user-provided green/yellow/orange/red class pairs as categorical signals—not decoration. Preserve the exact light tokens and risk fills/ink; dark mode changes surfaces/text but keeps contrast-safe badge variants. The ownable GeoCool brand color is the specified `#2563EB` blue.

**Layout paradigm:** a persistent navy identity bar and compact navigation rail anchor a wide map-first workspace. The overview places the Nagpur map on the primary side and the risk distribution/explanation rail beside it; on small screens, controls become a horizontal scroll row and content stacks without hiding the map or filters.

**Signature elements:** an always-visible `DEMO DATA` provenance badge; thin near-black ward outlines over pale cartographic fills; compact risk chips pairing the exact class fill and dark ink. The GeoCool mark is a geometric G/heat-contour glyph, not a stock image.

**Interaction and animation:** actions provide visible busy, completion and error feedback; keyboard focus remains visible and disabled controls stay legible. Map filters preserve state and show what changed. Use short (about 150–180 ms) transitions for hover/focus only; avoid animated score changes and honor `prefers-reduced-motion`.

**Typography:** system UI stack with Inter first when already available, then system sans-serif; 15–16 px body and dashboard labels, 14 px supporting copy, 12 px only for the map legend/badges, and 24–30 px page headings. No text below 12 px.

**Brand essence:** “A transparent urban heat-risk observatory for Nagpur planners, turning open geospatial inputs into explainable ward and street views.” Personality: precise, candid, civic-minded.

**Brand voice:** direct labels that state source and confidence, never promotional claims. Example lines: “AHP suitability is inverted to show risk—higher risk is worse.” “DEMO DATA: seeded values are synthetic, not official observations.”

**Wordmark/logo:** a compact GeoCool wordmark paired with a polygonal G formed by two contour bands and a small blue heat/signal dot; avoid generic stock illustration. **Signature brand color:** `#2563EB`.

## Material constraints and honest defaults

The original CSV contains only `edge_id,lst,ndvi,pm,population,slope,composite_suitability_score`; the wrapper may append `geometry_wkt,start_lat,start_lon,end_lat,end_lon` and may not invent feature columns. Earth Engine requires cached/authenticated credentials and exports may hit service limits; the tile fallback is local and no Drive export integration is claimed. Java’s source URL is only a legacy S3 fallback. No official NMC ward shapefile, physical IoT sensor or SMS/email gateway is supplied. The report’s Chapter 5 values are explicitly illustrative; the UI instead reads actual run outputs from `/api/metrics`, marking synthetic training output as demo. Demo Preview uses local SQLite; the requested PostGIS deployment is available via Docker Compose.
