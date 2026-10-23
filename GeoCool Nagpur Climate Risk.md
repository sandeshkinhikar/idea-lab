# GeoCool Nagpur Climate Risk

GeoCool is a report-driven prototype for explainable urban heat/climate-risk exploration. The interface is plain HTML/CSS/JavaScript; the service is FastAPI + Uvicorn. `DEMO DATA` is synthetic and must not be interpreted as official ward, street, sensor, or satellite observations.

## Demo mode

From the repository root:

```bash
python seed_demo.py
python -m pip install -r requirements.txt
uvicorn backend.app:app --host 0.0.0.0 --port 3000
```

Open `http://localhost:3000`. The seed creates `data/demo_data.json`, a SQLite demo database at `data/geocool_demo.sqlite`, and `data/processed/edge_suitability.csv`. Demo seed polygons and street edges are synthetic; the site labels them. You can set `DEMO_MODE=true` (the default) and run without Earth Engine credentials.

## Basemap

The map uses CartoDB Positron when `CARTO_API_KEY` is configured. CARTO's current public raster tiles require a key; request one from [CARTO](https://carto.com/basemaps/apikey/) and follow its [basemap FAQ](https://docs.carto.com/faqs/carto-basemaps). For Docker Compose, put it in `.env`; for local Uvicorn, export it in the server process environment. Restrict the browser key to the intended Preview/site referrer. The key is necessarily visible in browser tile requests, so do not reuse a private server credential. With no key, the map clearly labels an OpenStreetMap fallback instead of showing CARTO's key-required watermark. The fallback uses only normal interactive tile viewing, with visible attribution; follow the [OpenStreetMap Tile Usage Policy](https://operations.osmfoundation.org/policies/tiles/) and select a suitable hosted/self-hosted provider for higher-volume production use.

## PostgreSQL + PostGIS

```bash
cp .env.example .env
docker compose up --build
```

Compose starts PostGIS, loads `backend/schema.sql`, and serves the app at port 3000. `DATABASE_URL` is set by Compose to Postgres. For local `uvicorn`, set `DATABASE_URL=postgresql://geocool:geocool_demo@localhost:5432/geocool`. Change the demo password for non-local deployments. The real ward aggregation uses `ST_Intersects` when PostGIS is connected. The preview's local SQLite adapter is a demo fallback, not a PostGIS substitute.

## Real pipeline prerequisites

The originals are in `scripts/`: `1_download_rasters.py`, `2_build_real_dataset.py`, and `GisImageDownloader.java`. They are kept unmodified. The first requires Earth Engine access/cached authentication for project `dataset-510306`; the second requires the listed GeoPandas, OSMnx, rasterstats, scikit-learn, and raster files. The additive geometry wrapper is `scripts/build_dataset_patched.py`. Install optional dependencies with:

```bash
python -m pip install -r requirements-pipeline.txt
```

Configure credentials through your environment; never commit credential JSON or keys. The Java raster download is a warned legacy fallback to a 5 km URL, while the canonical Python study footprint is the expanded 20 km ROI. Large Earth Engine exports can fail at request-size limits; use the pipeline's bounded tile export and rasterio GeoTIFF mosaic fallback; direct Earth Engine Drive export is not implemented. Inspect its live logs and preflight results.

## API and operations

FastAPI serves interactive pages at `/`, `/alerts`, `/metrics`, `/data`, `/settings`, `/how`, and `/ward/{ward_id}`; named `.html` page shells are also available as aliases. `/manus-routes.json` declares both canonical routes and actual shells; API routes live under `/api/`. Useful endpoints include `/api/wards`, `/api/metrics`, `/api/alerts`, `/api/data/files`, `/api/health`, `/api/pipeline/jobs`, and the job-specific controls documented in `BUTTONS.md`.

Review `RECONCILIATION.md` for the ten known source/report issues, `LIMITATIONS.md` for provenance and unavailable external resources, and `BUTTONS.md` for button-to-effect mapping. The AHP suitability score is inverted as `risk_raw = 1 - composite_suitability_score`; labels are weakly supervised from the AHP score, not validated field truth. PM is a constant `65.0` placeholder. Chapter 5 report metrics are illustrative; the dashboard uses `/api/metrics` and marks seeded results as `DEMO DATA`.

## MQTT

The simulator/subscriber path uses `nagpur/wards/{ward_id}/sensors`. The subscriber persists accepted readings and sends threshold breaches through the same alert logic as the UI; a database-unique event key prevents duplicate ward/device/timestamp delivery from being stored twice, including across concurrent subscribers. Configure a broker only when intentionally testing it. Synthetic messages remain marked `DEMO DATA`; broker source alone does not verify a physical sensor. SMS/email has no gateway configured.
