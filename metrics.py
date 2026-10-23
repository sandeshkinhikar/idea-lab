"""Measured model-run metrics and actual CSV/PDF downloads."""
import csv
import io
import json
from fastapi import APIRouter, HTTPException
from fastapi.responses import Response, StreamingResponse
from backend.db import query_all, query_one

router = APIRouter()


def _decode(value):
    if value is None or value == "":
        return None
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return value


@router.get("/metrics")
def metrics():
    runs = query_all("SELECT * FROM model_runs ORDER BY trained_at DESC,id DESC")
    for run in runs:
        run["params"] = _decode(run.get("params"))
        run["confusion_matrix"] = _decode(run.get("confusion_matrix"))
        details = _decode(run.get("details")) or {}
        if isinstance(details, dict):
            for key in ("per_class", "classes", "confusion_labels", "kfold", "primary_task", "kind", "holdout", "validation", "class_balance_policy", "optional_model_note", "available", "status", "error"):
                if key in details:
                    run[key] = details[key]
        if "available" not in run:
            run["available"] = any(run.get(key) is not None for key in ("accuracy","precision","recall","f1","rmse","mae","r2"))
        run.setdefault("status", "complete" if run["available"] else "unavailable")
        run.pop("details", None)
    counts = {
        "wards": (query_one("SELECT COUNT(*) AS n FROM wards") or {}).get("n", 0),
        "edges": (query_one("SELECT COUNT(*) AS n FROM edges") or {}).get("n", 0),
        "alerts": (query_one("SELECT COUNT(*) AS n FROM alerts") or {}).get("n", 0),
        "sensor_readings": (query_one("SELECT COUNT(*) AS n FROM sensor_readings") or {}).get("n", 0),
    }
    demo_ward_count = (query_one("SELECT COUNT(*) AS n FROM wards WHERE provenance='demo_synthetic'") or {}).get("n", 0)
    demo = int(demo_ward_count or 0) > 0 or any(run.get("provenance") == "demo_synthetic" for run in runs)
    return {
        "metrics": runs,
        "model_runs": runs,
        "latest": runs[0] if runs else None,
        "status": "ready" if any(run.get("available") for run in runs) else ("unavailable" if runs else "not_trained"),
        "counts": counts,
        "demo": demo,
        "provenance": "demo_synthetic" if demo else ("source_configured" if counts["wards"] else "no_data"),
        "notice": "No real field labels are included; AHP-derived targets are weak labels. Null metrics mean no model has been trained. Values are copied from persisted model runs, not illustrative report figures.",
    }


def _csv_response(rows: list[dict], filename: str, empty_message: str):
    if not rows:
        raise HTTPException(status_code=503, detail=empty_message)
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=list(rows[0].keys()), extrasaction="ignore")
    writer.writeheader()
    writer.writerows(rows)
    return StreamingResponse(iter([stream.getvalue()]), media_type="text/csv; charset=utf-8", headers={"Content-Disposition": f"attachment; filename={filename}"})


@router.get("/export/csv")
def export_csv(scope: str = "edges"):
    if scope.strip().lower() in {"alert", "alerts"}:
        rows = query_all("SELECT a.id,a.ward_id,w.name AS ward_name,a.ts,a.severity,a.cause,a.precaution,a.status,a.provenance FROM alerts a LEFT JOIN wards w ON w.ward_id=a.ward_id ORDER BY a.ts DESC")
        return _csv_response(rows, "geocool_alerts.csv", "No alerts are available to export.")
    rows = query_all("SELECT e.edge_id,e.ward_id,w.name AS ward_name,e.lst,e.ndvi,e.pm,e.population,e.slope,e.ahp_suitability,e.ahp_risk,e.geometry_wkt,e.provenance FROM edges e JOIN wards w ON w.ward_id=e.ward_id ORDER BY e.ward_id,e.edge_id")
    return _csv_response(rows, "geocool_edges.csv", "No edge records are available to export. Initialize or refresh valid source rows first.")


@router.get("/export/alerts.csv")
def export_alerts_csv():
    rows = query_all("SELECT a.id,a.ward_id,w.name AS ward_name,a.ts,a.severity,a.cause,a.precaution,a.status,a.provenance FROM alerts a LEFT JOIN wards w ON w.ward_id=a.ward_id ORDER BY a.ts DESC")
    return _csv_response(rows, "geocool_alerts.csv", "No alerts are available to export.")


def _minimal_pdf(lines):
    """Build a small valid one-page PDF with Helvetica text and escaped literals."""
    def esc(text):
        return str(text).replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
    content = ["BT", "/F1 10 Tf", "50 790 Td"]
    for index, line in enumerate(lines[:48]):
        if index:
            content.append("0 -15 Td")
        content.append(f"({esc(line)[:115]}) Tj")
    content.append("ET")
    stream = "\n".join(content).encode("latin-1", "replace")
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 842] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream",
    ]
    data = bytearray(b"%PDF-1.4\n%GeoCool risk report\n")
    offsets = [0]
    for i, obj in enumerate(objects, 1):
        offsets.append(len(data))
        data.extend(f"{i} 0 obj\n".encode() + obj + b"\nendobj\n")
    xref = len(data)
    data.extend(f"xref\n0 {len(offsets)}\n0000000000 65535 f \n".encode())
    for offset in offsets[1:]:
        data.extend(f"{offset:010d} 00000 n \n".encode())
    data.extend(f"trailer\n<< /Size {len(offsets)} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode())
    return bytes(data)


@router.get("/export/pdf")
def export_pdf():
    wards = query_all("SELECT ward_id,name FROM wards ORDER BY ward_id")
    if not wards:
        raise HTTPException(status_code=503, detail="No ward records are available to export. Initialize or refresh valid ward data first.")
    lines = ["GeoCool Climate Risk Summary", "Synthetic entries are explicitly DEMO DATA; source records retain their provenance.", "AHP-derived labels are weak supervision, not field ground truth.", "", "Ward summary:"]
    for ward in wards:
        score = query_one("SELECT AVG(ahp_risk) AS risk FROM edges WHERE ward_id=?", (ward["ward_id"],)) or {}
        provenance = query_one("SELECT MAX(provenance) AS provenance FROM wards WHERE ward_id=?", (ward["ward_id"],)) or {}
        lines.append(f"{ward['ward_id']} | {ward['name']} | AHP risk {float(score.get('risk') or 0):.3f} | {provenance.get('provenance') or 'unknown'}")
    data = _minimal_pdf(lines)
    return Response(content=data, media_type="application/pdf", headers={"Content-Disposition": "attachment; filename=geocool_report.pdf"})
