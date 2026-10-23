from pathlib import Path
root=Path('/home/ubuntu/geocool')
def replace(path, old, new, expected=1):
    p=root/path; s=p.read_text(); count=s.count(old)
    if count!=expected: raise SystemExit(f'{path}: expected {expected} exact anchor(s), found {count}: {old[:100]!r}')
    p.write_text(s.replace(old,new,expected))

replace('plan.md',
'The ML layer trains a LinearRegression baseline, RandomForest classifier/regressor and XGBoost classifier/regressor; joblib artifacts live in `models/`, and each run’s metrics and validation detail are persisted in `model_runs`. Labels are configurable weak supervision derived from AHP-inverted risk—not field ground truth. Holdout uses geometry-derived spatial blocks when available or a temporal split if source timestamps exist; group-aware/stratified k-fold is used when sample counts permit. SHAP TreeExplainer is preferred when installed; documented permutation importance is the fallback. All scores show source, caveat and a plain-language explanation.',
'The ML layer trains a LinearRegression baseline, RandomForest classifier/regressor and XGBoost classifier/regressor; joblib artifacts live in `models/`, and each run’s metrics and validation detail are persisted in `model_runs`. Labels are configurable weak supervision derived from AHP-inverted risk—not field ground truth. Real CSV records are never trained edge-by-edge: `geometry_wkt` is assigned through batched PostGIS `ST_Intersects` to each intersecting official non-demo ward. If such boundaries are absent or geometry cannot be matched, the training endpoint returns an actionable unavailable/error result and never invents a ward ID or treats demo polygons as real. Holdout uses geometry-derived spatial blocks when available or a temporal split if source timestamps exist; group-aware/stratified k-fold is used when sample counts permit. SHAP TreeExplainer is preferred when installed; documented permutation importance is the fallback. All scores show source, caveat and a plain-language explanation.')
replace('plan.md',
'- `backend/pipeline_runner.py` and `backend/mqtt_simulator.py`: idempotent background jobs with preflight/log streaming, plus simulated publisher and real MQTT subscriber path.',
'- `backend/pipeline_runner.py` and `backend/mqtt_simulator.py`: preflighted, input-fingerprinted jobs that coalesce repeated equivalent runs (with explicit `force` support), timestamped logs, a simulated publisher, and an MQTT subscriber that persists readings and runs the shared alert thresholds. Duplicate `ward_id/device_id/timestamp` deliveries are ignored; physical-device identity is not asserted.')
replace('plan.md',
'- `frontend/assets/js/{api,map,charts,ui,contrast-audit}.js`: same-origin request/error/download helpers, Leaflet, Chart.js, page rendering/navigation, all actions and the developer-only WCAG audit.',
'- `frontend/assets/js/{api,map,charts,ui,contrast-audit}.js`: same-origin request/error/download helpers, Leaflet, Chart.js, page rendering/navigation, all actions, and a developer-only text-plus-focus-indicator contrast audit.')

replace('TODO.md',
'plus `/manus-routes.json` containing `/`, `/alerts`, `/metrics`, `/data`, `/settings`, `/how`, and `/ward/:ward_id`.',
'plus `/manus-routes.json` containing `/`, `/alerts`, `/metrics`, `/data`, `/settings`, `/how`, `/ward/:ward_id`, and the served `/index.html`, `/dashboard.html`, `/ward.html`, `/alerts.html`, `/metrics.html`, `/data.html`, `/settings.html`, and `/how.html` shells.')
replace('TODO.md',
'Every job has a unique ID and `queued/running/success/failed` state, is idempotent, has preflight checks and `pipeline_runs` logging, and streams timestamped stdout/stderr lines to the UI using SSE or WebSocket.',
'Every job has a unique ID and `queued/running/success/failed` state, coalesces repeated calls by explicit key or a stable input/options fingerprint (unless `force` requests a new run), has preflight checks and `pipeline_runs` logging, and streams timestamped stdout/stderr lines to the UI using SSE or WebSocket.')
replace('TODO.md',
'Implement paho-mqtt publisher simulator and subscriber for `nagpur/wards/{ward_id}/sensors`. Until a physical device is connected, every telemetry reading is visibly labeled simulated/demo.',
'Implement the paho-mqtt publisher simulator and subscriber for `nagpur/wards/{ward_id}/sensors`; subscriber readings use the same database/threshold/alert ingestion path. Duplicate `ward_id/device_id/timestamp` MQTT delivery is ignored. Simulated readings remain visibly demo; unverified broker device identity is not presented as a physically verified sensor.')
replace('TODO.md',
'Train from ward-aggregated `lst,ndvi,population,slope` plus constant `pm`; derive Low/Moderate/High/Critical targets from configurable AHP-inverted risk thresholds and mark the labels weakly supervised. Explain constant PM and target limitations.',
'Train from ward-aggregated `lst,ndvi,population,slope` plus constant `pm`; for real CSV geometry, batch `ST_Intersects` against official non-demo PostGIS wards and aggregate each source edge into every intersecting ward. If official polygons or usable geometry are absent, return an actionable error instead of edge-level training or a fabricated ward ID. Derive Low/Moderate/High/Critical targets from configurable AHP-inverted risk thresholds and mark the labels weakly supervised. Explain constant PM and target limitations.')

replace('RECONCILIATION.md',
'PostGIS schema and spatial aggregation use `ST_Intersects`; the ward output includes mean, max, and p90. The SQLite preview adapter is only for an offline seedable demo and is labeled synthetic.',
'PostGIS schema and spatial aggregation use `ST_Intersects`; the ward output includes mean, max, and p90. Real CSV geometry is assigned in batches to each intersecting non-demo official ward before ward-level training; if polygons/geometry are missing, the model route fails clearly rather than inventing a ward ID or training edge-by-edge. The SQLite preview adapter is only for an offline seedable demo and is labeled synthetic.')
replace('RECONCILIATION.md',
'Add a paho-mqtt simulator publisher and subscriber on `nagpur/wards/{ward_id}/sensors`. Synthetic readings and threshold alerts are explicitly simulated; a physical device is not claimed until one is configured. SMS/email delivery remains a visibly labeled stub with no gateway configured.',
'Add a paho-mqtt simulator publisher and a subscriber on `nagpur/wards/{ward_id}/sensors` that persists readings and invokes the same threshold/alert ingestion path as the UI. Duplicate deliveries keyed by ward/device/timestamp are ignored. Synthetic readings are labeled `DEMO DATA`; an unverified broker payload does not establish physical-device identity. SMS/email delivery remains a visibly labeled stub with no gateway configured.')

replace('LIMITATIONS.md',
'MQTT simulation publishes synthetic messages under `nagpur/wards/{ward_id}/sensors`; the path can consume a configured broker but does not mean a physical ESP32 is connected. SMS and email delivery gateways are not configured; any such notice must read `demo — no gateway configured`. In-app simulated threshold events are not external emergency alerts.',
'MQTT simulation publishes synthetic messages under `nagpur/wards/{ward_id}/sensors`; the subscriber persists broker messages through the same sensor-history and threshold-alert path, but a broker payload does not independently verify that a physical ESP32 is connected. Duplicate ward/device/timestamp delivery is ignored. Synthetic messages are marked `DEMO DATA`. SMS and email delivery gateways are not configured; any such notice must read `demo — no gateway configured`. In-app simulated threshold events are not external emergency alerts.')
replace('LIMITATIONS.md',
'Use the provided PostGIS environment for real `ST_Intersects` ward aggregation. Earth Engine and MQTT secrets are not committed in `.env.example` or the repository.',
'Use the provided PostGIS environment for real `ST_Intersects` ward aggregation. Real CSV training requires authoritative non-demo ward polygons and geometry that intersects them; without those inputs the model pipeline stops with an actionable message. Earth Engine and MQTT secrets are not committed in `.env.example` or the repository.')

replace('README.md',
'FastAPI serves interactive pages at `/`, `/alerts`, `/metrics`, `/data`, `/settings`, `/how`, and `/ward/{ward_id}`. `/manus-routes.json` declares those web pages; API routes live under `/api/`.',
'FastAPI serves interactive pages at `/`, `/alerts`, `/metrics`, `/data`, `/settings`, `/how`, and `/ward/{ward_id}`; named `.html` page shells are also available as aliases. `/manus-routes.json` declares both canonical routes and actual shells; API routes live under `/api/`.')
replace('README.md',
'The simulator/subscriber path uses `nagpur/wards/{ward_id}/sensors`. Configure an MQTT broker only when intentionally testing it. Simulated messages remain marked `simulated`; they are not physical sensor readings. SMS/email has no gateway configured.',
'The simulator/subscriber path uses `nagpur/wards/{ward_id}/sensors`. The subscriber persists accepted readings and sends threshold breaches through the same alert logic as the UI; duplicate ward/device/timestamp deliveries are ignored. Configure a broker only when intentionally testing it. Synthetic messages remain marked `DEMO DATA`; broker source alone does not verify a physical sensor. SMS/email has no gateway configured.')

replace('BUTTONS.md',
'| Explain | `GET /api/explain/{ward_id}` | Fetch top-three signed contribution breakdown, plain-English explanation, AHP weights, weak-label and PM caveats. |',
'| Ward/street explanation panel (loaded on detail or popup open) | `GET /api/explain/{ward_id}` or `GET /api/explain/{ward_id}?edge_id={edge_id}` | Fetch returned score/class, top-three signed contributions, plain-English explanation, AHP weights, weak-label and PM caveats; no separate inert Explain button is shown. |')
replace('BUTTONS.md',
'| Contrast audit | Client-side `window.runContrastAudit()` | Scan rendered DOM color pairs, console-log any contrast failures below WCAG AA, and display the count. |',
'| Contrast audit | Client-side `window.runContrastAudit()` | Scan rendered DOM text pairs and focus-indicator contrast, console-log text failures below WCAG AA or focus failures below 3:1, and display the result. |')
replace('BUTTONS.md',
'| View sensor history | `GET /api/wards/{ward_id}/sensor-history` | Display timestamped readings with simulated/real provenance. |',
'| View sensor history | `GET /api/wards/{ward_id}/sensor-history` | Display timestamped readings with record-level provenance. |\n| MQTT subscriber | `python -m backend.mqtt_simulator --subscribe` | Subscribe to `nagpur/wards/{ward_id}/sensors`, persist accepted readings, run the shared threshold/alert path, and ignore duplicate ward/device/timestamp deliveries. Physical-node identity is not independently verified. |')

replace('METHODOLOGY.md',
'6. **Convert to HRI and aggregate:** derive `risk_raw = 1 - composite_suitability_score`, classify using configurable weak-label thresholds, then aggregate street edges into wards (mean, maximum, p90) with PostGIS `ST_Intersects` when PostGIS is available.',
'6. **Convert to HRI and aggregate:** derive `risk_raw = 1 - composite_suitability_score`, classify using configurable weak-label thresholds, then aggregate street edges into wards (mean, maximum, p90) with PostGIS `ST_Intersects` when PostGIS is available. For real training, wrapper geometry is mapped to each intersecting official non-demo ward; if boundaries or usable geometry are absent, the model route stops instead of inventing ward IDs or training on edge-level labels.')
replace('METHODOLOGY.md',
'8. **Monitor and communicate risk:** in-app alerts and the `nagpur/wards/{ward_id}/sensors` MQTT simulation path power the alert views; physical sensors and SMS/email delivery are not claimed. `/api/alerts/simulate` is a synthetic end-to-end test.',
'8. **Monitor and communicate risk:** in-app alerts and the `nagpur/wards/{ward_id}/sensors` MQTT publisher/subscriber power the alert views and persisted sensor history; duplicate deliveries are idempotent, while physical sensors and SMS/email delivery are not claimed. `/api/alerts/simulate` is a synthetic end-to-end test.')
print('Project documentation is aligned to the implemented real-ward, MQTT, idempotency, provenance, route, and audit behavior.')