"""Apply persisted risk models to the latest usable ward/edge dataset."""
from __future__ import annotations

import math
from pathlib import Path
from typing import Any

import numpy as np

try:
    from .train import CLASS_NAMES, FEATURES, MODELS, _build_dataset, classify_risk
except ImportError:
    from train import CLASS_NAMES, FEATURES, MODELS, _build_dataset, classify_risk


def _load_model() -> tuple[dict[str, Any] | None, str | None]:
    try:
        import joblib
    except Exception:
        return None, None
    # Prefer RF regression for continuous risk; use the baseline next, then a
    # classifier whose class probabilities can be mapped to ordinal risk bins.
    priorities = (
        "geocool_randomforestregressor.joblib",
        "geocool_linearregression.joblib",
        "geocool_xgboostregressor.joblib",
        "geocool_randomforestclassifier.joblib",
        "geocool_xgboostclassifier.joblib",
    )
    for filename in priorities:
        path = MODELS / filename
        if path.is_file():
            try:
                artifact = joblib.load(path)
                if isinstance(artifact, dict) and artifact.get("estimator") is not None:
                    return artifact, path.name
            except Exception:
                continue
    return None, None


def _risk_estimate(artifact: dict[str, Any], matrix: np.ndarray) -> tuple[np.ndarray, str]:
    estimator = artifact["estimator"]
    kind = estimator.__class__.__name__.lower()
    if "classifier" not in kind and hasattr(estimator, "predict"):
        return np.clip(np.asarray(estimator.predict(matrix), dtype=float).reshape(-1), 0.0, 1.0), estimator.__class__.__name__
    class_codes = artifact.get("classifier_class_codes")
    encoded_labels = artifact.get("class_encoder_classes", list(CLASS_NAMES))
    def label_for(encoded_id: Any) -> str:
        local_id = int(encoded_id)
        global_id = int(class_codes[local_id]) if isinstance(class_codes, list) and 0 <= local_id < len(class_codes) else local_id
        try:
            return str(encoded_labels[global_id])
        except (IndexError, TypeError):
            return "Low"
    if hasattr(estimator, "predict_proba"):
        probabilities = np.asarray(estimator.predict_proba(matrix), dtype=float)
        class_ids = list(getattr(estimator, "classes_", range(probabilities.shape[1])))
        risks = []
        for row in probabilities:
            estimate = 0.0
            for col, encoded_id in enumerate(class_ids):
                try:
                    ordinal = CLASS_NAMES.index(label_for(encoded_id))
                except ValueError:
                    ordinal = 0
                estimate += float(row[col]) * ordinal / 3.0
            risks.append(estimate)
        return np.clip(np.asarray(risks), 0.0, 1.0), estimator.__class__.__name__
    predicted = np.asarray(estimator.predict(matrix), dtype=int).reshape(-1)
    risks = []
    for value in predicted:
        try:
            risks.append(CLASS_NAMES.index(label_for(value)) / 3.0)
        except ValueError:
            risks.append(0.0)
    return np.asarray(risks, dtype=float), estimator.__class__.__name__


def predict_all() -> list[dict[str, Any]]:
    """Return ward predictions or raise the actionable data-mapping failure.

    Returns an empty list only when there is no usable row or model artifact; it
    never silently substitutes edge-level, random, or AHP-only ML predictions.
    """
    rows, demo, provenance, data_error = _build_dataset()
    if data_error:
        raise RuntimeError(f"Prediction data could not be built: {data_error}")
    if not rows:
        return []
    artifact, model_filename = _load_model()
    if artifact is None:
        return []
    feature_names = list(artifact.get("feature_names", FEATURES))
    try:
        matrix = np.asarray([[float(row[name]) for name in feature_names] for row in rows], dtype=float)
    except (KeyError, TypeError, ValueError):
        return []
    if matrix.ndim != 2 or not np.isfinite(matrix).all():
        return []
    try:
        risks, algo = _risk_estimate(artifact, matrix)
    except Exception:
        return []
    output: list[dict[str, Any]] = []
    for row, risk in zip(rows, risks.tolist()):
        risk = min(1.0, max(0.0, float(risk)))
        output.append({
            "ward_id": row.get("ward_id"),
            "edge_id": row.get("sample_id"),
            "risk_score": round(risk, 6),
            "risk_class": classify_risk(risk),
            "ahp_risk_reference": round(float(row["risk"]), 6) if row.get("risk") is not None else None,
            "features": {name: float(row[name]) for name in feature_names if name in row},
            "algo": algo,
            "model_file": model_filename,
            "demo": bool(demo),
            "provenance": provenance,
            "weak_label_disclaimer": "Training target is AHP-derived risk (1 - suitability), not observed health or field ground truth.",
        })
    return output
