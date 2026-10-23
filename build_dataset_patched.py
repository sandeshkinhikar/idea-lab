#!/usr/bin/env python3
"""Run the original dataset builder and append its real OSMnx edge geometries.

The source builder remains untouched. ``runpy`` returns its global ``edges`` GeoDataFrame
after the original code completes, preserving row order and IDs without a second network
request or fabricated geometry/features.
"""
from __future__ import annotations

import csv
import os
import runpy
import sys
import tempfile
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
SOURCE = ROOT / "scripts" / "2_build_real_dataset.py"
OUTPUT = ROOT / "data" / "processed" / "edge_suitability.csv"
REQUIRED_FEATURES = {"edge_id", "lst", "ndvi", "pm", "population", "slope", "composite_suitability_score"}
GEOMETRY_COLUMNS = ("geometry_wkt", "start_lat", "start_lon", "end_lat", "end_lon")


def _geometry_points(geometry: Any) -> tuple[tuple[float, float], tuple[float, float]]:
    """Return first/last (longitude, latitude) coordinate from line geometry."""
    if geometry is None or getattr(geometry, "is_empty", True):
        raise ValueError("An output edge has no geometry.")
    kind = getattr(geometry, "geom_type", "")
    if kind == "LineString":
        coords = list(geometry.coords)
        if len(coords) < 2:
            raise ValueError("An edge LineString has fewer than two coordinates.")
        return (float(coords[0][0]), float(coords[0][1])), (float(coords[-1][0]), float(coords[-1][1]))
    if kind == "MultiLineString":
        parts = list(geometry.geoms)
        if not parts:
            raise ValueError("An edge MultiLineString has no component lines.")
        first = list(parts[0].coords)
        last = list(parts[-1].coords)
        if not first or not last:
            raise ValueError("An edge MultiLineString contains an empty component.")
        return (float(first[0][0]), float(first[0][1])), (float(last[-1][0]), float(last[-1][1]))
    raise ValueError(f"Expected line edge geometry, got {kind or type(geometry).__name__}.")


def _extract_edge_geometries(namespace: dict[str, Any]) -> tuple[list[str], list[Any]]:
    edges = namespace.get("edges")
    if edges is None or "geometry" not in getattr(edges, "columns", []):
        raise RuntimeError("Original dataset builder completed but did not expose its OSMnx edges geometry.")
    try:
        source_ids = [str(value) for value in edges["osmid"].tolist()]
    except Exception as exc:
        raise RuntimeError(f"Could not read edge_id values from source OSMnx graph: {exc}") from exc
    geometries = list(edges.geometry.tolist())
    if len(source_ids) != len(geometries):
        raise RuntimeError("Source edge IDs and geometries have different row counts.")
    crs = getattr(edges, "crs", None)
    if crs is not None and str(crs).lower() not in {"epsg:4326", "ogc:crs84", "4326"}:
        try:
            projected = edges.to_crs(epsg=4326)
            geometries = list(projected.geometry.tolist())
        except Exception as exc:
            raise RuntimeError(f"Could not transform source edge geometry to WGS84 coordinates: {exc}") from exc
    return source_ids, geometries


def patch_dataset() -> Path:
    if not SOURCE.is_file():
        raise FileNotFoundError(f"Required original dataset script is missing: {SOURCE}")
    old_cwd = Path.cwd()
    try:
        os.chdir(ROOT)
        # Executes the original script as supplied (without editing or copying it),
        # while exposing its final globals so the same exact OSMnx edges can be used.
        namespace = runpy.run_path(str(SOURCE))
    except ModuleNotFoundError as exc:
        dependency = exc.name or "an optional GIS package"
        raise RuntimeError(f"Dataset builder dependency {dependency!r} is missing. Install the pipeline extras from requirements-pipeline.txt, then retry.") from exc
    except Exception as exc:
        raise RuntimeError(f"Original dataset builder failed before geometry could be preserved: {exc}") from exc
    finally:
        os.chdir(old_cwd)

    if not OUTPUT.is_file():
        raise RuntimeError(f"Original builder completed without creating expected CSV: {OUTPUT}")
    source_ids, geometries = _extract_edge_geometries(namespace)
    with OUTPUT.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        fieldnames = list(reader.fieldnames or [])
        rows = list(reader)
    if not REQUIRED_FEATURES.issubset(fieldnames):
        missing = sorted(REQUIRED_FEATURES - set(fieldnames))
        raise RuntimeError(f"Builder CSV is missing required source columns: {', '.join(missing)}. No features are added by this wrapper.")
    if len(rows) != len(source_ids):
        raise RuntimeError(f"CSV has {len(rows)} rows but source OSMnx graph has {len(source_ids)} edges; refusing to attach misaligned geometry.")
    if "geometry_wkt" not in fieldnames:
        fieldnames.extend(GEOMETRY_COLUMNS)
    else:
        # Ensure all specified additions exist without duplicating preexisting columns.
        fieldnames.extend(column for column in GEOMETRY_COLUMNS if column not in fieldnames)

    for index, (row, edge_id, geometry) in enumerate(zip(rows, source_ids, geometries)):
        if str(row.get("edge_id")) != edge_id:
            raise RuntimeError(f"CSV/source edge alignment mismatch at row {index}: {row.get('edge_id')!r} != {edge_id!r}; refusing to attach incorrect geometry.")
        (start_lon, start_lat), (end_lon, end_lat) = _geometry_points(geometry)
        row.update({
            "geometry_wkt": geometry.wkt,
            "start_lat": format(start_lat, ".10f"),
            "start_lon": format(start_lon, ".10f"),
            "end_lat": format(end_lat, ".10f"),
            "end_lon": format(end_lon, ".10f"),
        })

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix="edge_suitability.", suffix=".csv", dir=OUTPUT.parent)
    try:
        with os.fdopen(fd, "w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="raise")
            writer.writeheader()
            writer.writerows(rows)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, OUTPUT)
    except Exception:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise
    print(f"Geometry wrapper added {', '.join(GEOMETRY_COLUMNS)} to {len(rows)} source rows: {OUTPUT}")
    return OUTPUT


def main() -> int:
    try:
        patch_dataset()
        return 0
    except Exception as exc:
        print(f"Dataset wrapper error: {exc}", file=sys.stderr, flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
