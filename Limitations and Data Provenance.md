# Limitations and Data Provenance

## Demo data is not a measured city dataset

The default seed command creates synthetic ward polygons, street lines, raster-like feature values, alerts, sensor readings, and any training runs based on them. They are marked `DEMO DATA` in the UI and tagged `demo_synthetic` in the generated seed. They are not official Nagpur Municipal Corporation boundaries, verified road network records, actual Earth Engine downloads, air-quality measurements, or physical IoT observations. Do not cite demo metrics as empirical urban-climate performance.

## Report figures and model labels

Chapter 5 values in the report are illustrative. The prototype does not reuse them as measured performance: `/api/metrics` reports values from its current recorded evaluation run, and provenance identifies synthetic input where applicable. Weak labels are generated from the inverted AHP score and configurable thresholds; they are not field-verified ground truth. Reported classification and regression metrics are therefore model-agreement/evaluation estimates under those labels, not proof of real-world risk accuracy.

## PM placeholder and input limits

The source builder assigns PM to the constant `65.0`; there is no PM raster or live PM sensor input in the supplied source files. The UI flags it as a constant placeholder. Population, NDVI, LST and slope only become real inputs when the actual pipeline has completed successfully and the source data provenance is available.

## GIS footprint and ward aggregation

Python source defines the canonical expanded footprint using `[78.89, 20.96, 79.29, 21.33]` and OSMnx `dist=20000`. The Java fallback embeds a 5 km S3 path and is not spatially consistent with that canonical extent. No official ward shapefile was supplied; seeded ward polygons are illustrative until an authoritative NMC boundary dataset is provided. PostGIS `ST_Intersects` is the intended spatial aggregation path; lightweight preview mode uses a seedable SQLite adapter when Postgres is unavailable.

## External prerequisites

Real raster jobs need working Earth Engine project access and cached/service-account credentials. Large ROI exports can exceed request limits, requiring tiling or Drive export. Dataset rebuilding needs OSMnx, GeoPandas, rasterstats, raster inputs, network access, and sufficient storage. The Java fallback needs a JDK and a reachable legacy URL; because its provider may not serve usable files, failures are shown and never converted into apparent success.

## IoT and alert-delivery stubs

MQTT simulation publishes synthetic messages under `nagpur/wards/{ward_id}/sensors`; the subscriber persists broker messages through the same sensor-history and threshold-alert path, but a broker payload does not independently verify that a physical ESP32 is connected. Duplicate ward/device/timestamp delivery is ignored. Synthetic messages are marked `DEMO DATA`. SMS and email delivery gateways are not configured; any such notice must read `demo — no gateway configured`. In-app simulated threshold events are not external emergency alerts.

## Preview/runtime differences

Docker Compose provides the requested PostgreSQL/PostGIS service and schema. A managed website preview without a Postgres service may use the local seeded SQLite demo adapter, which does not implement PostGIS spatial functions. Use the provided PostGIS environment for real `ST_Intersects` ward aggregation. Real CSV training requires authoritative non-demo ward polygons and geometry that intersects them; without those inputs the model pipeline stops with an actionable message. Earth Engine and MQTT secrets are not committed in `.env.example` or the repository.

## Basemap key

CARTO's public raster basemaps, including Positron, now require a browser basemap key. If `CARTO_API_KEY` is not configured, the map labels and uses an OpenStreetMap fallback; it does not hide or remove CARTO's key-required notice. The key is sent in public tile requests, so restrict it to the intended site/Preview referrer. See the [CARTO basemap FAQ](https://docs.carto.com/faqs/carto-basemaps). The keyless fallback uses the standard OSM raster service only for normal interactive viewing and includes its visible attribution; it must not be used for tile prefetch or offline downloads. Its availability is best-effort and is not a production SLA; use an appropriate tile provider for heavier public traffic.
