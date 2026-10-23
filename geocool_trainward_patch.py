from pathlib import Path
p=Path('/home/ubuntu/geocool/backend/ml/train.py')
s=p.read_text()
insert_at=s.index('\ndef _build_dataset()')
helper=r'''

def _map_real_csv_edges_to_wards(csv_rows: list[dict[str, str]]) -> tuple[dict[int, set[str]], str | None]:
    """Spatially assign real edge CSV rows to non-demo ward polygons via PostGIS.

    The source CSV is deliberately not given a ward_id column. If the required
    official ward polygons are absent, refuse to train a falsely ward-labeled
    model rather than assigning a fabricated or demo ward.
    """
    from backend.db import is_postgres, query_all

    if not is_postgres():
        return {}, "Real edge records need PostgreSQL/PostGIS and official non-demo ward polygons for ST_Intersects aggregation; edge-level fallback is disabled."
    try:
        real_wards = {str(row['ward_id']) for row in query_all("SELECT ward_id FROM wards WHERE provenance <> 'demo_synthetic'")}
    except Exception as exc:
        return {}, f"Could not inspect non-demo ward polygons: {type(exc).__name__}. Configure PostGIS before real-data training."
    if not real_wards:
        return {}, "No official non-demo ward polygons are loaded in PostGIS. Import authoritative ward boundaries before training real CSV records; no ward ID is inferred."

    assignments: dict[int, set[str]] = defaultdict(set)
    pending: list[tuple[int, str]] = []
    # Existing mappings are usable only when this source row has no geometry.
    try:
        existing_rows = query_all("SELECT edge_id,ward_id FROM edges WHERE provenance <> 'demo_synthetic' AND ward_id IS NOT NULL")
    except Exception:
        existing_rows = []
    existing_by_id = {str(row.get('edge_id')): str(row.get('ward_id')) for row in existing_rows if row.get('edge_id') and str(row.get('ward_id')) in real_wards}

    for index, row in enumerate(csv_rows):
        geometry = str(row.get('geometry_wkt') or '').strip()
        if geometry:
            pending.append((index, geometry))
        else:
            edge_id = str(row.get('edge_id') or '').strip()
            if edge_id in existing_by_id:
                assignments[index].add(existing_by_id[edge_id])

    try:
        for offset in range(0, len(pending), 400):
            chunk = pending[offset:offset + 400]
            values = ','.join(['(?,?)'] * len(chunk))
            params: list[Any] = []
            for index, geometry in chunk:
                params.extend((index, geometry))
            matches = query_all(
                f"SELECT source.row_index, ward.ward_id "
                f"FROM (VALUES {values}) AS source(row_index, geometry_wkt) "
                "JOIN wards AS ward ON ward.provenance <> 'demo_synthetic' "
                "AND ST_Intersects(ward.geom, ST_SetSRID(ST_GeomFromText(source.geometry_wkt),4326)) "
                "ORDER BY source.row_index, ward.ward_id",
                params,
            )
            for match in matches:
                ward_id = str(match.get('ward_id') or '')
                if ward_id in real_wards:
                    assignments[int(match['row_index'])].add(ward_id)
    except Exception as exc:
        return {}, f"PostGIS could not assign edge geometry to official ward polygons ({type(exc).__name__}); verify geometry_wkt and EPSG:4326 boundaries."

    mapped_count = sum(bool(value) for value in assignments.values())
    if not mapped_count:
        return {}, f"No source edges intersect the loaded non-demo ward polygons ({len(csv_rows)} CSV rows checked). Check that ward boundaries and edge geometry share EPSG:4326."
    return assignments, None
'''
s=s[:insert_at]+helper+s[insert_at:]
marker='            if rows:\n                return rows, True, "demo_synthetic", None\n'
if s.count(marker)!=1: raise SystemExit('demo aggregate insertion point mismatch')
start=s.index(marker)+len(marker)
end=s.index('\n\n\ndef load_training_data',start)
new=r'''

    if not csv_rows:
        return [], bool(demo_payload and demo_payload.get("demo")), "demo_synthetic" if demo_payload and demo_payload.get("demo") else "real_data", f"No usable CSV at {csv_path} and no ward-mapped demo records were available."

    assignments, mapping_error = _map_real_csv_edges_to_wards(csv_rows)
    if mapping_error:
        return [], False, "real_dataset", mapping_error
    by_ward: dict[str, list[dict[str, str]]] = defaultdict(list)
    for index, ward_ids in assignments.items():
        for ward_id in ward_ids:
            by_ward[ward_id].append(csv_rows[index])

    rows: list[dict[str, Any]] = []
    for ward_id, members in sorted(by_ward.items()):
        valid: list[tuple[dict[str, float], float]] = []
        for member in members:
            features = {feature: _float_or_none(member.get(feature)) for feature in FEATURES}
            suitability = _float_or_none(member.get("composite_suitability_score"))
            if any(value is None for value in features.values()) or suitability is None:
                continue
            valid.append(({key: float(value) for key, value in features.items()}, 1.0 - min(1.0, max(0.0, suitability))))
        if not valid:
            continue
        feature_means = {feature: sum(item[0][feature] for item in valid) / len(valid) for feature in FEATURES}
        # One training row per ward; an edge crossing polygon boundaries is
        # assigned to every intersected official ward, matching the spatial rule.
        risk = sum(item[1] for item in valid) / len(valid)
        rows.append({"ward_id": ward_id, **feature_means, "risk": risk, "label": classify_risk(risk), "spatial_group": ward_id, "provenance": "source_configured", "edge_count": len(valid)})
    if not rows:
        return [], False, "real_dataset", "No assigned ward has all five finite source features and a composite_suitability_score; no edge-level model was substituted."
    return rows, False, "source_configured", None'''
s=s[:start]+new+s[end:]
s=s.replace('"""Weakly supervised model training for edge/ward heat-risk records.', '"""Weakly supervised model training for ward-aggregated heat-risk records.', 1)
s=s.replace('"""Return ward-aggregated demo or CSV rows with targets/provenance."""', '"""Return ward-aggregated demo or real CSV rows with targets/provenance."""', 1)
p.write_text(s)

p=Path('/home/ubuntu/geocool/backend/ml/predict.py'); s=p.read_text()
s=s.replace('"""Return a predicted risk record per demo ward or real edge sample.', '"""Return a predicted risk record per ward-aggregated demo or real observation.', 1)
s=s.replace('Returns an empty list when no model artifact or usable features exist rather\n    than silently substituting random or AHP-only estimates for ML predictions.', 'Returns an empty list when no model artifact or ward-mapped features exist rather\n    than silently substituting edge-level, random, or AHP-only estimates for ML predictions.', 1)
p.write_text(s)
print('Added batched PostGIS ST_Intersects assignment and one-row-per-ward real-data training; edge-level fallback is refused.')