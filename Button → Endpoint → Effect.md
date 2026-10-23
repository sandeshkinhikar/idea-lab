# Button → Endpoint → Effect

Every listed control must present a loading state, a success result or visible error, a keyboard focus ring, and a contrast-safe disabled state. Client-side export/filter/theme controls are marked as such; a control is omitted if its effect is not implemented.

| Button/control | Endpoint/action | Effect |
|---|---|---|
| Refresh | `POST /api/refresh` | Re-read available CSV data, recompute/refresh summaries, update dashboard, and announce the outcome. |
| Download Rasters (GEE) | `POST /api/pipeline/raster` | Start Earth Engine downloader job and expose job ID, preflight, status, and timestamped logs. |
| Fallback Download (Java) | `POST /api/pipeline/java` | Start legacy Java downloader; first display its 5 km scope warning. |
| Rebuild Dataset | `POST /api/pipeline/dataset` | Check four source rasters, run original dataset builder through geometry-preserving wrapper, stream logs. |
| Train Model | `POST /api/model/train` | Train LinearRegression, RandomForestClassifier/Regressor, and XGBoostClassifier/Regressor; persist available artifacts and measured model-run rows/metrics, with any unavailable model and cause shown. |
| Predict | `POST /api/model/predict` | Apply available models to eligible wards/edges and refresh displayed scores. |
| Ward/street explanation panel (loaded on detail or popup open) | `GET /api/explain/{ward_id}` or `GET /api/explain/{ward_id}?edge_id={edge_id}` | Fetch returned score/class, top-three signed contributions, plain-English explanation, AHP weights, weak-label and PM caveats; no separate inert Explain button is shown. |
| Acknowledge alert | `PATCH /api/alerts/{id}` with `{"status":"ack"}` | Persist acknowledged state and update open/active counters. |
| Resolve alert | `PATCH /api/alerts/{id}` with `{"status":"resolved"}` | Persist resolution and update counters/list. |
| Simulate threshold breach | `POST /api/alerts/simulate` | Insert synthetic sensor reading, run threshold classification, and create/update an alert end-to-end; label output simulated. |
| Save thresholds | `PUT /api/settings/thresholds` | Persist threshold settings and reclassify derived scores. |
| Export CSV | `GET /api/export/csv` | Download a generated CSV from current API data. |
| Export PDF | `GET /api/export/pdf` | Download a generated PDF summary of current API data. |
| Search ward/edge | `GET /api/wards` and `GET /api/wards/{ward_id}/streets` | Find a matching ward or edge, pan/zoom, and open its detail/explainability panel. |
| Risk/layer controls | Client-side map action, data loaded from `GET /api/wards` and street endpoint | Change displayed risk/LST/NDVI/slope/population/AHP/street layer. |
| KPI card filter | Client-side map/table filter from `GET /api/wards` and `GET /api/alerts` | Filter visible locations/rows to the selected monitored/critical/alerts/model summary. |
| Ward/Street view | `GET /api/wards` or `/api/wards/{ward_id}/streets` | Switch between aggregated ward polygons and street edges. |
| View sensor history | `GET /api/wards/{ward_id}/sensor-history` | Display timestamped readings with record-level provenance. |
| MQTT subscriber | `python -m backend.mqtt_simulator --subscribe` | Subscribe to `nagpur/wards/{ward_id}/sensors`, persist accepted readings, run the shared threshold/alert path, and ignore duplicate ward/device/timestamp deliveries. Physical-node identity is not independently verified. |
| Open pipeline job | `GET /api/pipeline/jobs/{job_id}` and SSE `GET /api/pipeline/jobs/{job_id}/events` | Refresh job state and receive timestamped console output. |
| Alerts export | `GET /api/export/csv?scope=alerts` | Download filtered/current alerts as CSV. |
| Run experiment | `POST /api/model/experiment` | Train/evaluate another run with split ratio, folds, and seed and add it to run comparison. |
| Theme toggle | Client-side persisted preference | Switch between accessible light and dark tokens, without changing data. |
| Save refresh interval | Client-side persisted preference | Set and use an interval for the dashboard refresh action. |
| Contrast audit | Client-side `window.runContrastAudit()` | Scan rendered DOM text pairs and focus-indicator contrast, console-log text failures below WCAG AA or focus failures below 3:1, and display the result. |
| Demo Mode switch | `PUT /api/settings/demo-mode` | Persist the user preference; synthetic seed records remain marked `DEMO DATA` even when the preference is off, because a preference change cannot convert synthetic records into real observations. |
| Zoom to ward | Leaflet map action | Pan/fit bounds to selected ward polygon. |
| View streets | `GET /api/wards/{ward_id}/streets` | Open selected ward's street-edge list/map layer. |
