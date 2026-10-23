"""Local explanations for persisted heat-risk models.

Tree models use SHAP TreeExplainer when it imports and runs successfully. The
fallback is scikit-learn permutation importance (global magnitude) combined with
a local one-feature-to-median perturbation (direction); this is clearly labeled
and is not presented as SHAP.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np

try:
    from .predict import _load_model, _risk_estimate
    from .train import AHP_WEIGHTS, FEATURES, _build_dataset
except ImportError:
    from predict import _load_model, _risk_estimate
    from train import AHP_WEIGHTS, FEATURES, _build_dataset


def _unavailable(ward_id: str, reason: str, *, demo: bool = False) -> dict[str, Any]:
    return {"ward_id": ward_id, "available": False, "reason": reason, "contributions": [], "ahp_weights": dict(AHP_WEIGHTS), "constant_input_flags": {"pm": {"constant": True, "value": 65.0, "warning": "PM is a constant placeholder (65.0), not a measured driver."}}, "weak_label_disclaimer": "Model labels are weak supervision derived from AHP suitability, not field ground truth.", "demo": demo}


def _get_shap_values(estimator: Any, matrix: np.ndarray, row: np.ndarray) -> tuple[list[float], str] | None:
    try:
        import shap
        explainer = shap.TreeExplainer(estimator)
        values = explainer.shap_values(row.reshape(1, -1))
        if isinstance(values, list):
            # Regressors generally return a single matrix; for a classifier use
            # the highest-risk class output, if the classifier is selected.
            values = values[-1]
        if hasattr(values, "values"):
            values = values.values
        array = np.asarray(values, dtype=float)
        if array.ndim == 3:
            array = array[0, :, -1]
        elif array.ndim == 2:
            array = array[0]
        if array.ndim != 1 or len(array) != row.shape[0] or not np.isfinite(array).all():
            return None
        return [float(value) for value in array], "SHAP TreeExplainer"
    except Exception:
        return None


def explain_ward(ward_id: str) -> dict[str, Any]:
    """Explain the model estimate for a ward with signed feature contributions."""
    ward_id = str(ward_id)
    rows, demo, provenance, load_error = _build_dataset()
    if load_error or len(rows) < 2:
        return _unavailable(ward_id, load_error or f"Only {len(rows)} usable rows; insufficient data for explanation.", demo=demo)
    artifact, model_filename = _load_model()
    if artifact is None:
        return _unavailable(ward_id, "No trained model artifact is available. Run train_models first; no contribution values are fabricated.", demo=demo)
    found = None
    for row in rows:
        if str(row.get("ward_id", row.get("sample_id", ""))) == ward_id:
            found = row
            break
    if found is None:
        if provenance == "real_dataset":
            reason = "No ward_id exists in the real CSV; this explanation API currently requires a ward-mapped row."
        else:
            reason = f"Ward {ward_id!r} was not found in the ward-aggregated demo dataset."
        return _unavailable(ward_id, reason, demo=demo)

    names = list(artifact.get("feature_names", FEATURES))
    try:
        matrix = np.asarray([[float(row[name]) for name in names] for row in rows], dtype=float)
        point = np.asarray([float(found[name]) for name in names], dtype=float)
    except (KeyError, TypeError, ValueError) as exc:
        return _unavailable(ward_id, f"Required model features are missing: {exc}", demo=demo)
    if not np.isfinite(matrix).all() or not np.isfinite(point).all():
        return _unavailable(ward_id, "Feature values contain missing or non-finite values.", demo=demo)

    estimator = artifact["estimator"]
    shap_result = _get_shap_values(estimator, matrix, point)
    method = "SHAP TreeExplainer"
    signed_values: list[float]
    permutation_importances: dict[str, float] = {}
    if shap_result is not None:
        signed_values, method = shap_result
    else:
        try:
            from sklearn.inspection import permutation_importance as compute_permutation_importance
            is_classifier = "classifier" in estimator.__class__.__name__.lower()
            class_codes = artifact.get("classifier_class_codes")
            if is_classifier:
                encoder_classes = artifact.get("class_encoder_classes", list(CLASS_NAMES))
                eligible = []
                target_values = []
                for index, row in enumerate(rows):
                    try:
                        global_code = encoder_classes.index(str(row["label"]))
                        if isinstance(class_codes, list):
                            if global_code not in class_codes:
                                continue
                            target_values.append(class_codes.index(global_code))
                        else:
                            target_values.append(global_code)
                        eligible.append(index)
                    except (KeyError, ValueError):
                        continue
                if len(eligible) < 2:
                    raise ValueError("Not enough training-class rows to compute classifier permutation importance.")
                importance_result = compute_permutation_importance(estimator, matrix[eligible], np.asarray(target_values), n_repeats=8, random_state=42, scoring="accuracy")
            else:
                target = np.asarray([float(row["risk"]) for row in rows], dtype=float)
                importance_result = compute_permutation_importance(estimator, matrix, target, n_repeats=8, random_state=42, scoring="neg_mean_absolute_error")
            raw = np.maximum(0.0, np.asarray(importance_result.importances_mean, dtype=float))
            permutation_importances = {name: float(raw[i]) for i, name in enumerate(names)}
            base_value = float(_risk_estimate(artifact, point.reshape(1, -1))[0][0])
            medians = np.median(matrix, axis=0)
            signed_values = []
            # Direction is the local change in predicted risk when a feature is
            # moved to its training median. Importance provides its global
            # permutation-derived magnitude/ranking (the method is not SHAP).
            local_changes = []
            for i in range(len(names)):
                modified = point.copy()
                modified[i] = medians[i]
                changed = float(_risk_estimate(artifact, modified.reshape(1, -1))[0][0])
                local_changes.append(base_value - changed)
            raw_local = np.asarray(local_changes, dtype=float)
            norm = float(np.sum(np.abs(raw_local)))
            imp_norm = float(np.sum(raw))
            if norm > 0 and imp_norm > 0:
                for i, change in enumerate(raw_local):
                    magnitude = (abs(float(change)) / norm) * imp_norm
                    signed_values.append(magnitude if change >= 0 else -magnitude)
            else:
                signed_values = [float(v) for v in raw_local]
            method = "Permutation importance + local median perturbation (documented fallback; signed direction is local risk delta)"
        except Exception as exc:
            return _unavailable(ward_id, f"SHAP is unavailable and permutation-importance fallback failed: {exc}", demo=demo)

    try:
        risk_values, algo = _risk_estimate(artifact, point.reshape(1, -1))
        predicted = float(risk_values[0])
    except Exception as exc:
        return _unavailable(ward_id, f"Could not obtain a model estimate for this ward: {exc}", demo=demo)
    pm_values = [float(row["pm"]) for row in rows if row.get("pm") is not None]
    pm_constant = bool(pm_values and max(pm_values) - min(pm_values) <= 1e-9 and abs(pm_values[0] - 65.0) <= 1e-6)
    contributions = []
    for index, name in enumerate(names):
        value = float(signed_values[index]) if index < len(signed_values) else 0.0
        contributions.append({
            "feature": name,
            "contribution": round(value, 8),
            "magnitude": round(abs(value), 8),
            "direction": "increases risk" if value > 1e-10 else "decreases risk" if value < -1e-10 else "neutral",
            "ahp_weight": AHP_WEIGHTS.get(name),
            "input_value": float(point[index]),
            "constant_input": bool(name == "pm" and pm_constant),
            "permutation_importance": round(permutation_importances.get(name, 0.0), 8) if permutation_importances else None,
            "note": "Constant placeholder; not a measured driver." if name == "pm" and pm_constant else None,
        })
    contributions.sort(key=lambda item: item["magnitude"], reverse=True)
    top = contributions[:3]
    if top:
        clauses = [f"{item['feature']} {item['direction']} (signed contribution {item['contribution']:+.4f})" for item in top]
        sentence = f"The model estimates {ward_id} risk at {predicted:.3f} ({' / '.join(clauses)}); compare the AHP weights as reference weights, not learned effects."
    else:
        sentence = f"The model estimates {ward_id} risk at {predicted:.3f}, but no feature contributions were available."
    return {
        "ward_id": ward_id,
        "available": True,
        "predicted_risk": round(predicted, 6),
        "risk_class": __import__("backend.ml.train", fromlist=["classify_risk"]).classify_risk(predicted),
        "algo": algo,
        "model_file": model_filename,
        "method": method,
        "contributions": contributions,
        "top_contributions": top,
        "explanation": sentence,
        "ahp_weights": dict(AHP_WEIGHTS),
        "constant_input_flags": {"pm": {"constant": pm_constant, "value": 65.0, "warning": "PM is a constant placeholder (65.0), not a measured driver." if pm_constant else None}},
        "weak_label_disclaimer": "Weak supervision: training labels are risk = 1 - AHP suitability, not field ground truth; feature effects do not establish causality.",
        "demo": bool(demo),
        "provenance": provenance,
    }
