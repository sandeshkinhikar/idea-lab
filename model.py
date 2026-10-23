"""Model training and prediction adapters; ML package is optional at runtime."""
from __future__ import annotations
import json
from datetime import datetime, timezone
from typing import Any
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from backend.db import execute, get_setting, query_all

router = APIRouter()

class ExperimentRequest(BaseModel):
    split_ratio: float = Field(default=0.2, gt=0.05, lt=0.5)
    folds: int = Field(default=5, ge=2, le=20)
    seed: int = Field(default=42, ge=0)

class TrainRequest(BaseModel):
    options: dict[str, Any] = Field(default_factory=dict)
    split_ratio: float | None = Field(default=None, gt=0.05, lt=0.5)
    folds: int | None = Field(default=None, ge=2, le=20)
    seed: int | None = Field(default=None, ge=0)

class PredictRequest(BaseModel):
    ward_id: str | None = None
    features: dict[str, float] | None = None


def _call_train(kwargs):
    try:
        from backend.ml.train import train_models
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Model training is unavailable: {exc}. Add backend.ml.train.train_models to enable it.") from exc
    try:
        result = train_models(**kwargs)
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"Model training failed: {exc}") from exc
    if not isinstance(result, dict):
        result = {"result":result}
    # Persist returned evaluation metrics, without manufacturing metric values.
    timestamp = datetime.now(timezone.utc).isoformat()
    keys = ("accuracy","precision","recall","f1","rmse","mae","r2","confusion_matrix")
    candidates = result.get("metrics", result)
    if isinstance(candidates, dict):
        candidates = [candidates]
    if isinstance(candidates, list):
        persisted = 0
        for candidate in candidates:
            if not isinstance(candidate, dict):
                continue
            available = candidate.get("available") is not False and not candidate.get("error")
            values = [candidate.get(k) for k in keys] if available else [None] * len(keys)
            algo = candidate.get("algo",result.get("algo",result.get("algorithm","unspecified")))
            provenance = candidate.get("provenance",result.get("provenance","demo_synthetic"))
            if result.get("demo") or result.get("provenance") == "demo_synthetic":
                provenance = "demo_synthetic"
            detail_keys = ("per_class", "classes", "confusion_labels", "kfold", "primary_task", "kind", "holdout", "validation", "class_balance_policy", "optional_model_note")
            details = {key:candidate[key] for key in detail_keys if key in candidate}
            details.update({"available":available,"status":"complete" if available else "unavailable"})
            if not available:
                details["error"] = str(candidate.get("error") or "Model run was unavailable; no metric values were produced.")
            execute("INSERT INTO model_runs(algo,trained_at,params,accuracy,precision,recall,f1,rmse,mae,r2,confusion_matrix,provenance,details) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",(str(algo),timestamp,json.dumps(kwargs,default=str),*values[:7],json.dumps(values[7]) if values[7] is not None else None,str(provenance),json.dumps(details,default=str)))
            persisted += 1
        if result.get("demo") or result.get("provenance"):
            result.setdefault("provenance",result.get("provenance","demo_synthetic"))
        result["persisted_model_runs"] = persisted
    return result

@router.post("/model/train")
def train(body: TrainRequest | None = None):
    options = body.options.copy() if body else {}
    if body:
        options.update({key:value for key,value in {"split_ratio":body.split_ratio,"folds":body.folds,"seed":body.seed}.items() if value is not None})
    result = _call_train(options)
    return {"result":result,"demo":bool(result.get("demo")),"provenance":result.get("provenance","unknown")}

@router.post("/model/predict")
def predict(body: PredictRequest | None = None):
    try:
        from backend.ml.predict import predict_all
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Model prediction is unavailable: {exc}. Add backend.ml.predict.predict_all to enable it.") from exc
    if body and body.features:
        raise HTTPException(status_code=422, detail="Custom feature-vector prediction is not supported by predict_all; provide no features and optionally filter by ward_id.")
    try:
        result = predict_all()
        if body and body.ward_id:
            result = [item for item in result if str(item.get("ward_id")) == body.ward_id]
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"Model prediction failed: {exc}") from exc
    thresholds = {"low_max":.25,"moderate_max":.5,"high_max":.75,**get_setting("thresholds",{})}
    def classify(score):
        if score < thresholds["low_max"]: return "Low"
        if score < thresholds["moderate_max"]: return "Moderate"
        if score < thresholds["high_max"]: return "High"
        return "Critical"
    persisted = 0
    for item in result:
        if not isinstance(item,dict) or not item.get("ward_id") or item.get("risk_score") is None:
            continue
        item["risk_class"] = classify(float(item["risk_score"]))
        provenance = item.get("provenance", "demo_synthetic")
        if item.get("demo"):
            provenance = "demo_synthetic"
        execute("INSERT INTO risk_scores(ward_id,edge_id,ts,ml_score,ml_class,ahp_score,ahp_class,contributions,provenance) VALUES(?,?,?,?,?,?,?,?,?)", (item["ward_id"],item.get("edge_id"),datetime.now(timezone.utc).isoformat(),float(item["risk_score"]),item["risk_class"],item.get("ahp_risk_reference"),classify(float(item["ahp_risk_reference"])) if item.get("ahp_risk_reference") is not None else None,None,provenance))
        persisted += 1
    demo = any(item.get("demo") or item.get("provenance") == "demo_synthetic" for item in result)
    provenance = "demo_synthetic" if demo else (result[0].get("provenance") if result else "no_predictions")
    return {"predictions":result,"persisted":persisted,"demo":demo,"provenance":provenance,"warning":"Prediction provenance depends on the trained model and input data; demo seed features remain synthetic. Train a model first if the prediction list is empty."}

@router.post("/model/experiment")
def experiment(body: ExperimentRequest):
    options = {"split_ratio":body.split_ratio,"folds":body.folds,"seed":body.seed}
    result = _call_train(options)
    return {"experiment":options,"result":result,"demo":bool(result.get("demo")),"provenance":result.get("provenance","unknown")}
