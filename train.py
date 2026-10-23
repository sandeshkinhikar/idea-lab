"""Weakly supervised model training for ward-aggregated heat-risk records.

Targets are not field observations: risk is derived as 1 - AHP suitability. Demo
ward aggregation uses the ward_id mapping in data/demo_data.json; CSV features stay
restricted to the documented source columns plus wrapper-added geometry fields.
"""
from __future__ import annotations

import csv
import json
import math
import os
import re
from collections import defaultdict
from functools import lru_cache
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
MODELS = ROOT / "models"
FEATURES = ("lst", "ndvi", "pm", "population", "slope")
ALLOWED_CSV_COLUMNS = set(FEATURES) | {"edge_id", "composite_suitability_score", "geometry_wkt", "start_lat", "start_lon", "end_lat", "end_lon"}
CLASS_NAMES = ("Low", "Moderate", "High", "Critical")
AHP_WEIGHTS = {"lst": 0.35, "ndvi": 0.25, "pm": 0.20, "population": 0.10, "slope": 0.10}


def _spatial_group(lat: float | None, lon: float | None) -> str | None:
    if lat is None or lon is None or not math.isfinite(lat) or not math.isfinite(lon):
        return None
    # A roughly 5 km geographic block at Nagpur latitude, used only to avoid
    # placing neighboring samples into both sides of the spatial holdout.
    return f"{math.floor(lat * 20)}:{math.floor(lon * 20)}"


def _row_spatial_group(row: dict[str, Any]) -> str | None:
    lat, lon = _float_or_none(row.get("start_lat")), _float_or_none(row.get("start_lon"))
    if lat is None or lon is None:
        match = re.search(r"([-+]?\d+(?:\.\d+)?)\s+([-+]?\d+(?:\.\d+)?)", str(row.get("geometry_wkt") or ""))
        if match:
            lon, lat = float(match.group(1)), float(match.group(2))
    return _spatial_group(lat, lon)


@lru_cache(maxsize=1)
def _thresholds() -> tuple[float, float, float]:
    defaults = (float(os.getenv("RISK_LOW_MAX", "0.25")), float(os.getenv("RISK_MODERATE_MAX", "0.50")), float(os.getenv("RISK_HIGH_MAX", "0.75")))
    try:
        # Persisted settings take precedence, so the UI's threshold editor also
        # controls future weak-label training and prediction classifications.
        from backend.db import get_setting
        saved = get_setting("thresholds", {})
        values = (float(saved.get("low_max", defaults[0])), float(saved.get("moderate_max", defaults[1])), float(saved.get("high_max", defaults[2]))) if isinstance(saved, dict) else defaults
        if not (0 < values[0] < values[1] < values[2] < 1):
            raise ValueError
        return values
    except Exception:
        try:
            if not (0 < defaults[0] < defaults[1] < defaults[2] < 1):
                raise ValueError
            return defaults
        except ValueError:
            return (0.25, 0.50, 0.75)


def classify_risk(risk: float) -> str:
    low, moderate, high = _thresholds()
    if risk < low:
        return "Low"
    if risk < moderate:
        return "Moderate"
    if risk < high:
        return "High"
    return "Critical"


def _float_or_none(value: Any) -> float | None:
    try:
        number = float(value)
        return number if math.isfinite(number) else None
    except (TypeError, ValueError):
        return None


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        cols = set(reader.fieldnames or [])
        unknown = cols - ALLOWED_CSV_COLUMNS
        if unknown:
            raise ValueError(f"CSV contains unsupported feature/column(s): {', '.join(sorted(unknown))}. Allowed columns are: {', '.join(sorted(ALLOWED_CSV_COLUMNS))}.")
        return [dict(row) for row in reader]



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

def _build_dataset() -> tuple[list[dict[str, Any]], bool, str, str | None]:
    """Return ward-aggregated demo or real CSV rows with targets/provenance."""
    demo_path = DATA / "demo_data.json"
    csv_path = DATA / "processed" / "edge_suitability.csv"
    demo_payload: dict[str, Any] | None = None
    if demo_path.is_file():
        try:
            demo_payload = json.loads(demo_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            demo_payload = None

    csv_rows: list[dict[str, str]] = []
    if csv_path.is_file():
        csv_rows = _read_csv(csv_path)

    spatial_by_ward: dict[str, str] = {}
    if demo_payload:
        for ward in demo_payload.get("wards", []):
            center = ward.get("center") or {}
            group = _spatial_group(_float_or_none(center.get("lat")), _float_or_none(center.get("lon")))
            if group and ward.get("ward_id"):
                spatial_by_ward[str(ward["ward_id"])] = group

    # The generated demo CSV intentionally has no ward_id. Use edge_id mapping
    # from the companion seed JSON only when its IDs actually match the CSV;
    # a real builder CSV can coexist with an older demo JSON in demo mode.
    if demo_payload and demo_payload.get("demo") and demo_payload.get("edges"):
        edge_map = {str(edge.get("edge_id")): edge for edge in demo_payload["edges"]}
        source_rows: list[dict[str, Any]] = []
        if csv_rows:
            for row in csv_rows:
                source = edge_map.get(str(row.get("edge_id")))
                if source:
                    joined = dict(source)
                    for feature in FEATURES:
                        if row.get(feature) not in (None, ""):
                            joined[feature] = row[feature]
                    if row.get("composite_suitability_score") not in (None, ""):
                        joined["composite_suitability_score"] = row["composite_suitability_score"]
                    source_rows.append(joined)
        is_demo_csv = not csv_rows or len(source_rows) >= 0.9 * len(csv_rows)
        if is_demo_csv:
            if not source_rows:
                source_rows = list(demo_payload["edges"])
            by_ward: dict[str, list[dict[str, Any]]] = defaultdict(list)
            for edge in source_rows:
                ward_id = edge_map.get(str(edge.get("edge_id")), edge).get("ward_id")
                if ward_id:
                    by_ward[str(ward_id)].append(edge)
            rows = []
            for ward_id, members in sorted(by_ward.items()):
                x: dict[str, float] = {}
                usable = True
                for feature in FEATURES:
                    vals = [_float_or_none(member.get(feature)) for member in members]
                    vals = [value for value in vals if value is not None]
                    if not vals:
                        usable = False
                        break
                    x[feature] = sum(vals) / len(vals)
                labels = []
                for member in members:
                    suitability = _float_or_none(member.get("composite_suitability_score", member.get("ahp_suitability")))
                    if suitability is not None:
                        labels.append(1.0 - min(1.0, max(0.0, suitability)))
                if usable and labels:
                    risk = sum(labels) / len(labels)
                    rows.append({"ward_id": ward_id, **x, "risk": risk, "label": classify_risk(risk), "spatial_group": spatial_by_ward.get(ward_id), "provenance": "demo_synthetic"})
            if rows:
                return rows, True, "demo_synthetic", None


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
    return rows, False, "source_configured", None


def load_training_data() -> tuple[list[dict[str, Any]], bool, str, str | None]:
    """Shared loader used by training, predictions, and explanations."""
    return _build_dataset()


def _metrics(y_true: list[float], predicted: list[float], y_class_true: list[str], y_class_pred: list[str], labels: list[str], *, regression: bool) -> dict[str, Any]:
    try:
        from sklearn.metrics import accuracy_score, confusion_matrix, f1_score, mean_absolute_error, mean_squared_error, precision_score, r2_score, recall_score
        accuracy = float(accuracy_score(y_class_true, y_class_pred))
        precision = float(precision_score(y_class_true, y_class_pred, labels=labels, average="macro", zero_division=0))
        recall = float(recall_score(y_class_true, y_class_pred, labels=labels, average="macro", zero_division=0))
        f1 = float(f1_score(y_class_true, y_class_pred, labels=labels, average="macro", zero_division=0))
        matrix = confusion_matrix(y_class_true, y_class_pred, labels=labels).tolist()
        per_class = {}
        p_each = precision_score(y_class_true, y_class_pred, labels=labels, average=None, zero_division=0)
        r_each = recall_score(y_class_true, y_class_pred, labels=labels, average=None, zero_division=0)
        f_each = f1_score(y_class_true, y_class_pred, labels=labels, average=None, zero_division=0)
        for i, name in enumerate(labels):
            per_class[name] = {"precision": float(p_each[i]), "recall": float(r_each[i]), "f1": float(f_each[i]), "support": int(sum(label == name for label in y_class_true))}
        result: dict[str, Any] = {"accuracy": accuracy, "precision": precision, "recall": recall, "f1": f1, "confusion_matrix": matrix, "confusion_labels": labels, "per_class": per_class}
        if regression:
            result.update({"rmse": float(math.sqrt(mean_squared_error(y_true, predicted))), "mae": float(mean_absolute_error(y_true, predicted)), "r2": float(r2_score(y_true, predicted)) if len(y_true) > 1 else None})
        else:
            result.update({"rmse": None, "mae": None, "r2": None})
        return result
    except Exception as exc:
        return {"accuracy": None, "precision": None, "recall": None, "f1": None, "rmse": None, "mae": None, "r2": None, "confusion_matrix": [], "confusion_labels": labels, "per_class": {}, "metrics_error": str(exc)}


def _kfold_report(estimator: Any, x: Any, y: Any, labels: list[str], *, classifier: bool, folds: int, seed: int, groups: Any = None) -> dict[str, Any] | None:
    n = len(y)
    if folds < 2 or n < 3:
        return None
    k = min(folds, n)
    try:
        from sklearn.base import clone
        from sklearn.model_selection import GroupKFold, KFold, StratifiedKFold, cross_val_predict
        use_groups = groups is not None and len(set(str(value) for value in groups)) >= 2
        if use_groups:
            k = min(k, len(set(str(value) for value in groups)))
            if k < 2:
                return {"available": False, "reason": "Not enough distinct spatial blocks for group k-fold validation."}
            splitter = GroupKFold(n_splits=k)
            pred = cross_val_predict(clone(estimator), x, y, cv=splitter, groups=groups)
            method = "spatial GroupKFold"
        else:
            method = "stratified k-fold" if classifier else "k-fold"
        if classifier:
            class_values = sorted(set(y.tolist() if hasattr(y, "tolist") else list(y)))
            counts = {value: sum(item == value for item in y) for value in class_values}
            positive = [count for count in counts.values() if count]
            if len(positive) < 2:
                return {"available": False, "reason": "At least two classes are required for classification k-fold validation."}
            if not use_groups:
                if min(positive) < 2:
                    return {"available": False, "reason": "Not enough examples per class for stratified k-fold validation."}
                k = min(k, min(positive))
                if k < 2:
                    return {"available": False, "reason": "Not enough examples per class for stratified k-fold validation."}
                splitter = StratifiedKFold(n_splits=k, shuffle=True, random_state=seed)
                pred = cross_val_predict(clone(estimator), x, y, cv=splitter)
            from sklearn.metrics import accuracy_score, f1_score, precision_score, recall_score
            display_labels = list(range(len(labels)))
            return {"available": True, "method": method, "folds": k, "accuracy": float(accuracy_score(y, pred)), "precision": float(precision_score(y, pred, labels=display_labels, average="macro", zero_division=0)), "recall": float(recall_score(y, pred, labels=display_labels, average="macro", zero_division=0)), "f1": float(f1_score(y, pred, labels=display_labels, average="macro", zero_division=0))}
        if not use_groups:
            splitter = KFold(n_splits=k, shuffle=True, random_state=seed)
            pred = cross_val_predict(clone(estimator), x, y, cv=splitter)
        from sklearn.metrics import mean_absolute_error, mean_squared_error, r2_score
        true_class = [classify_risk(float(value)) for value in y]
        pred_class = [classify_risk(float(value)) for value in pred]
        return {"available": True, "method": method, "folds": k, "rmse": float(math.sqrt(mean_squared_error(y, pred))), "mae": float(mean_absolute_error(y, pred)), "r2": float(r2_score(y, pred)) if n > 1 else None, "accuracy": float(sum(a == b for a, b in zip(true_class, pred_class)) / n)}
    except Exception as exc:
        return {"available": False, "reason": f"Cross-validation unavailable: {exc}"}


def train_models(split_ratio: float = 0.2, folds: int = 5, seed: int = 42) -> dict[str, Any]:
    """Train baseline, RF classifier/regressor, and XGBoost models.

    The output metrics are computed on a seeded holdout when feasible, plus
    k-fold estimates where the sample/class counts support them.
    """
    if not 0.05 <= float(split_ratio) < 0.5:
        raise ValueError("split_ratio must be between 0.05 and 0.49.")
    rows, demo, provenance, load_error = _build_dataset()
    base = {"demo": demo, "provenance": provenance, "labeling": "Weak supervision: target risk = 1 - composite_suitability_score (AHP suitability), not field ground truth.", "thresholds": {"low_max": _thresholds()[0], "moderate_max": _thresholds()[1], "high_max": _thresholds()[2]}, "ahp_weights": dict(AHP_WEIGHTS), "data_summary": {"samples": len(rows), "unit": "ward" if provenance == "demo_synthetic" else "edge", "features": list(FEATURES)}, "metrics": [], "models": [], "unavailable_reason": None}
    if load_error or len(rows) < 5:
        base["unavailable_reason"] = load_error or f"Only {len(rows)} usable rows; at least 5 are needed for a holdout model."
        return base
    try:
        import numpy as np
        from sklearn.ensemble import RandomForestClassifier, RandomForestRegressor
        from sklearn.linear_model import LinearRegression
        from sklearn.model_selection import train_test_split
        from sklearn.preprocessing import LabelEncoder
        import joblib
    except Exception as exc:
        base["unavailable_reason"] = f"scikit-learn/numpy/joblib unavailable: {exc}"
        return base

    x = np.asarray([[float(row[key]) for key in FEATURES] for row in rows], dtype=float)
    y_risk = np.asarray([float(row["risk"]) for row in rows], dtype=float)
    y_class_text = np.asarray([str(row["label"]) for row in rows], dtype=object)
    labels: list[str] = list(CLASS_NAMES)
    encoder = LabelEncoder().fit(labels)
    y_class = encoder.transform(y_class_text)
    indexes = np.arange(len(rows))
    raw_groups = [row.get("spatial_group") for row in rows]
    usable_groups = all(value not in (None, "") for value in raw_groups) and len(set(raw_groups)) >= 3
    cv_groups = np.asarray(raw_groups, dtype=object) if usable_groups else None
    timestamps = [row.get("timestamp") or row.get("ts") for row in rows]
    validation: dict[str, Any] = {"split": "random_holdout", "spatial_groups": len(set(raw_groups)) if usable_groups else 0, "temporal_holdout_available": False}
    if all(value not in (None, "") for value in timestamps) and len(set(str(value) for value in timestamps)) >= 2:
        ordered = np.asarray(sorted(indexes, key=lambda index: str(timestamps[int(index)])))
        test_count = max(1, int(math.ceil(len(ordered) * float(split_ratio))))
        train_idx, test_idx = ordered[:-test_count], ordered[-test_count:]
        validation = {"split": "temporal_holdout", "temporal_holdout_available": True, "train_end": str(timestamps[int(train_idx[-1])]), "test_start": str(timestamps[int(test_idx[0])]), "spatial_groups": len(set(raw_groups)) if usable_groups else 0}
    elif usable_groups:
        from sklearn.model_selection import GroupShuffleSplit
        groups = np.asarray(raw_groups, dtype=object)
        required_classes = {int(value) for value in np.unique(y_class) if int(sum(y_class == value)) >= 2}
        candidates = []
        for attempt in range(30):
            splitter = GroupShuffleSplit(n_splits=1, test_size=float(split_ratio), random_state=int(seed) + attempt)
            candidate_train, candidate_test = next(splitter.split(x, y_class, groups=groups))
            present = {int(value) for value in np.unique(y_class[candidate_train])}
            candidates.append((candidate_train, candidate_test, present))
            if required_classes.issubset(present) and len(candidate_train) >= 2 and len(candidate_test) >= 1:
                train_idx, test_idx = candidate_train, candidate_test
                break
        else:
            best = max(candidates, key=lambda candidate: len(candidate[2] & required_classes))
            train_idx, test_idx = best[0], best[1]
        validation = {"split": "spatial_group_holdout", "temporal_holdout_available": False, "spatial_groups": len(set(raw_groups)), "held_out_groups": sorted(set(str(groups[index]) for index in test_idx)), "note": "Spatial grid blocks are kept on one side of the holdout; temporal fields were absent from the source feature table."}
    else:
        try:
            train_idx, test_idx = train_test_split(indexes, test_size=float(split_ratio), random_state=int(seed), stratify=y_class if min((int(sum(y_class == i)) for i in np.unique(y_class)), default=0) >= 2 else None)
        except Exception:
            train_idx, test_idx = train_test_split(indexes, test_size=float(split_ratio), random_state=int(seed))
        validation = {"split": "stratified_random_holdout" if len(np.unique(y_class)) > 1 else "random_holdout", "temporal_holdout_available": False, "spatial_groups": len(set(value for value in raw_groups if value)) if raw_groups else 0, "note": "No usable time field or at least three geometry-derived spatial groups were available."}
    base["validation"] = validation
    base["data_summary"]["spatial_blocks"] = validation.get("spatial_groups", 0)
    if len(train_idx) < 2 or len(test_idx) < 1:
        base["unavailable_reason"] = "The requested holdout leaves too few training or test samples."
        return base

    models: list[tuple[str, str, Any]] = [
        ("LinearRegression", "regressor", LinearRegression()),
        ("RandomForestClassifier", "classifier", RandomForestClassifier(n_estimators=250, random_state=int(seed), class_weight="balanced", min_samples_leaf=1)),
        ("RandomForestRegressor", "regressor", RandomForestRegressor(n_estimators=250, random_state=int(seed), min_samples_leaf=1)),
    ]
    xgb_error = None
    try:
        from xgboost import XGBClassifier, XGBRegressor
        models.extend([
            ("XGBoostClassifier", "classifier", XGBClassifier(n_estimators=120, max_depth=3, learning_rate=0.06, subsample=0.9, colsample_bytree=0.9, objective="multi:softprob", eval_metric="mlogloss", random_state=int(seed), n_jobs=1)),
            ("XGBoostRegressor", "regressor", XGBRegressor(n_estimators=120, max_depth=3, learning_rate=0.06, subsample=0.9, colsample_bytree=0.9, objective="reg:squarederror", random_state=int(seed), n_jobs=1)),
        ])
    except Exception as exc:
        xgb_error = f"XGBoost not included: {exc}"

    MODELS.mkdir(parents=True, exist_ok=True)
    for name, kind, estimator in models:
        classifier_class_codes = None
        try:
            if kind == "classifier":
                y = y_class
                fit_options = {}
                class_balance_policy = "class_weight='balanced'" if getattr(estimator, "class_weight", None) == "balanced" else "balanced sample_weight"
                fit_target = y[train_idx]
                if name == "XGBoostClassifier":
                    # XGBoost's sklearn wrapper requires labels 0..K-1. A
                    # spatial holdout can leave a valid but non-contiguous
                    # subset (for example classes 1,2,3) on the training side.
                    classifier_class_codes = sorted(int(value) for value in np.unique(fit_target))
                    if len(classifier_class_codes) < 2:
                        raise ValueError("XGBoost classifier needs at least two observed classes in the spatial/temporal training split.")
                    fit_target = np.asarray([classifier_class_codes.index(int(value)) for value in fit_target], dtype=int)
                if getattr(estimator, "class_weight", None) != "balanced":
                    from sklearn.utils.class_weight import compute_sample_weight
                    fit_options["sample_weight"] = compute_sample_weight("balanced", fit_target)
                estimator.fit(x[train_idx], fit_target, **fit_options)
                local_prediction = estimator.predict(x[test_idx]).astype(int)
                if classifier_class_codes is not None:
                    global_prediction = np.asarray([classifier_class_codes[int(np.clip(value, 0, len(classifier_class_codes) - 1))] for value in local_prediction], dtype=int)
                    class_code_map = classifier_class_codes
                else:
                    global_prediction = np.clip(local_prediction, 0, len(labels) - 1)
                    class_code_map = list(range(len(encoder.classes_)))
                predicted_class = encoder.inverse_transform(global_prediction)
                if hasattr(estimator, "predict_proba"):
                    probabilities = estimator.predict_proba(x[test_idx])
                    class_order = list(getattr(estimator, "classes_", []))
                    predicted_risk = []
                    for row_probs in probabilities:
                        estimated = 0.0
                        for column, encoded_label in enumerate(class_order):
                            global_code = class_code_map[int(encoded_label)] if int(encoded_label) < len(class_code_map) else int(encoded_label)
                            class_name = encoder.inverse_transform([global_code])[0]
                            estimated += float(row_probs[column]) * (labels.index(class_name) / 3.0)
                        predicted_risk.append(estimated)
                else:
                    predicted_risk = [labels.index(value) / 3.0 for value in predicted_class]
                true_classes = [str(v) for v in y_class_text[test_idx]]
                result = _metrics(y_risk[test_idx].tolist(), list(predicted_risk), true_classes, list(predicted_class), labels, regression=True)
                result["primary_task"] = "classification"
                cv_target = y[train_idx]
                if classifier_class_codes is not None:
                    cv_target = np.asarray([classifier_class_codes.index(int(value)) for value in cv_target], dtype=int)
                result["kfold"] = _kfold_report(estimator, x[train_idx], cv_target, labels, classifier=True, folds=int(folds), seed=int(seed), groups=cv_groups[train_idx] if cv_groups is not None else None)
            else:
                y = y_risk
                estimator.fit(x[train_idx], y[train_idx])
                prediction = np.asarray(estimator.predict(x[test_idx]), dtype=float).reshape(-1)
                prediction = np.clip(prediction, 0.0, 1.0)
                true_classes = [str(v) for v in y_class_text[test_idx]]
                predicted_class = [classify_risk(float(value)) for value in prediction]
                result = _metrics(y_risk[test_idx].tolist(), prediction.tolist(), true_classes, predicted_class, labels, regression=True)
                result["primary_task"] = "regression + risk-bin classes"
                result["kfold"] = _kfold_report(estimator, x[train_idx], y_risk[train_idx], labels, classifier=False, folds=int(folds), seed=int(seed), groups=cv_groups[train_idx] if cv_groups is not None else None)
            artifact_path = MODELS / f"geocool_{name.lower()}.joblib"
            artifact = {"estimator": estimator, "feature_names": list(FEATURES), "classes": list(CLASS_NAMES), "class_encoder_classes": [str(value) for value in encoder.classes_], "classifier_class_codes": classifier_class_codes, "trained_at": __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat(), "provenance": provenance, "demo": demo, "weak_label": True, "train_sample_ids": [rows[i].get("ward_id", rows[i].get("sample_id")) for i in train_idx], "training_X": x[train_idx], "training_y": y_risk[train_idx]}
            try:
                joblib.dump(artifact, artifact_path)
                model_path = str(artifact_path)
            except Exception as exc:
                model_path = None
                result["save_warning"] = f"Could not persist model artifact: {exc}"
            result.update({"algo": name, "kind": kind, "holdout": {"train_samples": int(len(train_idx)), "test_samples": int(len(test_idx)), "split_ratio": float(split_ratio), "seed": int(seed)}, "validation": validation, "class_balance_policy": class_balance_policy if kind == "classifier" else "not_applicable", "model_path": model_path, "provenance": provenance})
            base["metrics"].append(result)
            base["models"].append({"algo": name, "kind": kind, "path": model_path})
        except Exception as exc:
            base["metrics"].append({"algo": name, "kind": kind, "available": False, "error": str(exc), "provenance": provenance})
    if xgb_error:
        base["optional_model_note"] = xgb_error
    base["holdout_sample_ids"] = {"train": [rows[i].get("ward_id", rows[i].get("sample_id")) for i in train_idx], "test": [rows[i].get("ward_id", rows[i].get("sample_id")) for i in test_idx]}
    if not base["metrics"]:
        base["unavailable_reason"] = "All model estimators failed; see per-algorithm errors."
    return base
