"""Background pipeline dispatch and job status/event endpoints."""
from __future__ import annotations
import asyncio
import importlib
import inspect
import json
from datetime import datetime, timezone
from typing import Any, cast
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from backend.db import query_all, query_one

router = APIRouter()

class PipelineRequest(BaseModel):
    options: dict[str, Any] = Field(default_factory=dict)


def _runner():
    try:
        return importlib.import_module("backend.pipeline_runner")
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Pipeline runner is unavailable: {exc}. Add backend.pipeline_runner to enable jobs.") from exc


def _dispatch(job: str, options: dict[str, Any]):
    runner = _runner()
    names = {"raster":["start_raster_pipeline","run_raster_pipeline","launch_job","start_pipeline"],"java":["start_java_pipeline","run_java_pipeline","launch_job","start_pipeline"],"dataset":["start_dataset_pipeline","run_dataset_pipeline","launch_job","start_pipeline"],"train_and_predict":["launch_job","start_pipeline"]}[job]
    for name in names:
        fn = getattr(runner,name,None)
        if not callable(fn):
            continue
        try:
            if name == "launch_job":
                result = fn(job, **options)
            elif name == "start_pipeline":
                result = fn(job, **options)
            else:
                result = fn(**options)
        except TypeError:
            try:
                result = fn(job, options)
            except Exception as exc:
                raise HTTPException(status_code=422, detail=f"Could not start {job} pipeline: {exc}") from exc
        except Exception as exc:
            raise HTTPException(status_code=422, detail=f"Could not start {job} pipeline: {exc}") from exc
        if inspect.isawaitable(result):
            raise HTTPException(status_code=500, detail="pipeline_runner returned an awaitable from a synchronous dispatch function; provide a synchronous background-job launcher.")
        return result
    raise HTTPException(status_code=503, detail=f"pipeline_runner is installed but exposes no supported background launcher for '{job}'.")


def _job_id(result):
    if isinstance(result, dict):
        return result.get("job_id",result.get("id",result.get("job")))
    return getattr(result,"job_id",getattr(result,"id",result))


def _start(job: str, body: PipelineRequest | None):
    options = body.options if body else {}
    result = _dispatch(job, options)
    job_id = _job_id(result)
    if not job_id:
        raise HTTPException(status_code=502, detail="Pipeline runner started without returning a job identifier.")
    return {"job_id":str(job_id),"job":job,"status":"queued","result":result,"message":"Job dispatched to pipeline_runner; outputs are not considered verified until the runner reports completion."}

@router.post("/pipeline/raster")
def raster(body: PipelineRequest | None = None):
    return _start("raster",body)

@router.post("/pipeline/java")
def java(body: PipelineRequest | None = None):
    return _start("java",body)

@router.post("/pipeline/dataset")
def dataset(body: PipelineRequest | None = None):
    return _start("dataset",body)

@router.post("/pipeline/model")
@router.post("/pipeline/train-predict", include_in_schema=False)
def train_predict(body: PipelineRequest | None = None):
    return _start("train_and_predict",body)

@router.get("/pipeline/preflight/{job_name}")
def pipeline_preflight(job_name: str):
    result = _runner().preflight(job_name)
    return result

@router.get("/pipeline/jobs")
def jobs():
    runner = None
    try:
        runner = _runner()
    except HTTPException:
        pass
    for name in ("list_jobs","get_jobs","jobs"):
        method = getattr(runner,name,None) if runner else None
        if callable(method):
            try:
                value = method()
                if not inspect.isawaitable(value):
                    live = value if isinstance(value,list) else []
                    persisted = query_all("SELECT id AS record_id,COALESCE(job_id,CAST(id AS TEXT)) AS job_id,job,status,started_at,finished_at,log FROM pipeline_runs ORDER BY id DESC")
                    seen = {str(item.get("job_id")) for item in live if isinstance(item,dict)}
                    return {"jobs":live + [row for row in persisted if str(row.get("job_id")) not in seen]}
            except Exception:
                break
    return {"jobs":query_all("SELECT id AS record_id,COALESCE(job_id,CAST(id AS TEXT)) AS job_id,job,status,started_at,finished_at,log FROM pipeline_runs ORDER BY id DESC")}

@router.get("/pipeline/jobs/{job_id}")
def job_status(job_id: str):
    runner = None
    try:
        runner = _runner()
    except HTTPException:
        pass
    for name in ("get_job","job_status","get_job_status"):
        method = getattr(runner,name,None) if runner else None
        if callable(method):
            try:
                result = method(job_id)
                if not inspect.isawaitable(result):
                    if result is not None:
                        return {"job":result}
            except HTTPException:
                raise
            except Exception as exc:
                raise HTTPException(status_code=502,detail=f"Could not read pipeline job status: {exc}") from exc
    row = query_one("SELECT id AS record_id,COALESCE(job_id,CAST(id AS TEXT)) AS job_id,job,status,started_at,finished_at,log FROM pipeline_runs WHERE job_id=? OR CAST(id AS TEXT)=? ORDER BY id DESC LIMIT 1", (job_id,job_id))
    if row is None:
        raise HTTPException(status_code=404,detail=f"Pipeline job {job_id} was not found in the runner or local history.")
    return {"job":row}

@router.get("/pipeline/jobs/{job_id}/events")
async def job_events(job_id: str, request: Request):
    runner = None
    try:
        runner = _runner()
    except HTTPException:
        pass
    get_job = getattr(runner,"get_job",None) if runner else None
    if callable(get_job) and get_job(job_id) is not None:
        async def live_events():
            index = 0
            while not await request.is_disconnected():
                state = get_job(job_id)
                if state is None:
                    yield "event: error\ndata: " + json.dumps({"detail":"Pipeline job disappeared from runner state."}) + "\n\n"
                    break
                lines = state.get("log_lines",[]) if isinstance(state,dict) else []
                for item in lines[index:]:
                    yield f"data: {json.dumps(item)}\n\n"
                index = len(lines)
                if isinstance(state,dict) and state.get("status") in {"success","failed","cancelled","error"}:
                    yield f"event: status\ndata: {json.dumps(state,default=str)}\n\n"
                    break
                await asyncio.sleep(.35)
        return StreamingResponse(live_events(),media_type="text/event-stream",headers={"Cache-Control":"no-cache","X-Accel-Buffering":"no"})
    for name in ("stream_events","job_events","events"):
        method = getattr(runner,name,None) if runner else None
        if callable(method):
            try:
                stream = method(job_id)
                if inspect.isawaitable(stream):
                    stream = await stream
                if hasattr(stream,"__aiter__"):
                    async def relay():
                        async for event in cast(Any, stream):
                            if await request.is_disconnected():
                                break
                            payload = event if isinstance(event,str) else json.dumps(event,default=str)
                            yield f"data: {payload}\n\n"
                    return StreamingResponse(relay(),media_type="text/event-stream",headers={"Cache-Control":"no-cache","X-Accel-Buffering":"no"})
                if hasattr(stream,"__iter__"):
                    async def relay_sync():
                        for event in cast(Any, stream):
                            if await request.is_disconnected():
                                break
                            payload = event if isinstance(event,str) else json.dumps(event,default=str)
                            yield f"data: {payload}\n\n"
                            await asyncio.sleep(0)
                    return StreamingResponse(relay_sync(),media_type="text/event-stream",headers={"Cache-Control":"no-cache","X-Accel-Buffering":"no"})
            except Exception as exc:
                raise HTTPException(status_code=502,detail=f"Could not stream job events: {exc}") from exc
    # Fallback streams the persisted runner log/status until terminal state or disconnect.
    async def poll():
        last = None
        for _ in range(120):
            if await request.is_disconnected():
                break
            row = query_one("SELECT id AS record_id,COALESCE(job_id,CAST(id AS TEXT)) AS job_id,job,status,started_at,finished_at,log FROM pipeline_runs WHERE job_id=? OR CAST(id AS TEXT)=? ORDER BY id DESC LIMIT 1", (job_id,job_id))
            if row is None:
                yield "event: error\ndata: {\"detail\":\"No persisted event stream for this runner job.\"}\n\n"
                break
            serialized = json.dumps(row,default=str)
            if serialized != last:
                yield f"data: {serialized}\n\n"
                last = serialized
            if row.get("status") in {"completed","success","failed","cancelled","error"}:
                break
            await asyncio.sleep(.5)
    return StreamingResponse(poll(),media_type="text/event-stream",headers={"Cache-Control":"no-cache","X-Accel-Buffering":"no"})
