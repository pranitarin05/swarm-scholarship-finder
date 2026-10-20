"""FastAPI app: run creation, SSE event stream, result retrieval."""
from __future__ import annotations

import asyncio
import json
import uuid
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse

from .config import settings
from .events import emit_event, run_store
from .graph import run_pipeline
from .state import Preferences, RunState

app = FastAPI(title="ScholarSwarm API", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


def _parse_json_form(value: str, fallback):
    if not value:
        return fallback
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return fallback


@app.post("/api/runs")
async def create_run(
    resume: UploadFile = File(...),
    marksheet: Optional[UploadFile] = File(None),
    preferences: str = Form("{}"),
    demo_mode: bool = Form(False),
    inject_failure: str = Form(""),
) -> dict:
    """Store uploads, create a run, start the agent graph in the background."""
    if demo_mode:
        run_id = "demo-" + uuid.uuid4().hex[:8]
        run_store.create(run_id)
        asyncio.create_task(_replay_demo(run_id))
        return {"run_id": run_id, "demo_mode": True}

    run_id = uuid.uuid4().hex[:12]
    ctx = run_store.create(run_id)
    run_dir = ctx.run_dir()
    prefs_raw = _parse_json_form(preferences, {})
    prefs = Preferences.model_validate(prefs_raw) if isinstance(prefs_raw, dict) else Preferences()

    resume_path = run_dir / (Path(resume.filename or "resume.pdf").name)
    resume_path.write_bytes(await resume.read())
    marksheet_path = ""
    if marksheet is not None and (marksheet.filename or ""):
        ms_path = run_dir / Path(marksheet.filename).name
        ms_path.write_bytes(await marksheet.read())
        marksheet_path = str(ms_path)

    state = RunState(
        run_id=run_id,
        preferences=prefs,
        resume_path=str(resume_path),
        marksheet_path=marksheet_path,
        github_url=prefs_raw.get("github_url", ""),
        extra_links=prefs_raw.get("extra_links", []),
        inject_fail=inject_failure,
    )
    if inject_failure:
        ctx.failure_mode = inject_failure
    state_json = state.model_dump_json()
    (run_dir / "state.json").write_text(state_json, encoding="utf-8")
    asyncio.create_task(_execute(run_id, state, inject_fail=inject_fail))
    return {"run_id": run_id}


async def _execute(run_id: str, state: RunState, inject_fail: str = "") -> None:
    """Run the graph; always mark the run finished so SSE can close."""
    try:
        final = await run_pipeline(state, inject_fail=inject_fail)
        result = final.model_dump(mode="json")
        run_store.set_result(run_id, result)
        emit_event(run_id, "supervisor", "done", "Run complete", {"status": "done"})
        run_store.set_status(run_id, "done")
    except Exception as exc:
        run_store.set_status(run_id, "failed")
        emit_event(
            run_id, "supervisor", "error",
            f"Run failed: {type(exc).__name__}: {exc}",
        )
        run_store.set_result(run_id, {"status": "failed", "error": str(exc)})
        emit_event(run_id, "supervisor", "done", "Run ended with errors")


async def _replay_demo(run_id: str) -> None:
    """Demo mode: replay cached events with realistic pacing."""
    events_path = settings.demo_cache_dir / "events.json"
    result_path = settings.demo_cache_dir / "result.json"
    if not events_path.exists() or not result_path.exists():
        emit_event(run_id, "supervisor", "error", "Demo cache missing")
        emit_event(run_id, "supervisor", "done", "Demo unavailable")
        return
    events = json.loads(events_path.read_text(encoding="utf-8"))
    result = json.loads(result_path.read_text(encoding="utf-8"))
    ctx = run_store.get(run_id)
    if ctx:
        ctx.status = "running"
    for raw in events:
        emit_event(
            run_id,
            raw.get("agent", "supervisor"),
            raw.get("type", "result"),
            raw.get("message", ""),
            raw.get("data", {}),
        )
        await asyncio.sleep(0.35)
    run_store.set_result(run_id, result)
    emit_event(run_id, "supervisor", "done", "Demo run complete")
    run_store.set_status(run_id, "done")


@app.get("/api/runs/{run_id}/events")
async def stream_events(run_id: str) -> StreamingResponse:
    """SSE stream of every agent event for the live timeline."""
    return StreamingResponse(
        run_store.stream(run_id),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/api/runs/{run_id}/result")
async def get_result(run_id: str) -> JSONResponse:
    """Final run state: profile, candidates, top 5, sop, audit."""
    ctx = run_store.get(run_id)
    if ctx is None:
        disk = settings.runs_dir / run_id / "result.json"
        if disk.exists():
            return JSONResponse(json.loads(disk.read_text(encoding="utf-8")))
        raise HTTPException(status_code=404, detail="run not found")
    if not ctx.result and ctx.status != "done":
        return JSONResponse({"status": ctx.status, "events": len(ctx.events)})
    return JSONResponse(ctx.result)


@app.get("/api/runs/{run_id}/status")
async def get_status(run_id: str) -> dict:
    ctx = run_store.get(run_id)
    if ctx is None:
        raise HTTPException(status_code=404, detail="run not found")
    return {"status": ctx.status, "events": len(ctx.events)}


@app.get("/api/health")
async def health() -> dict:
    return {"ok": True, "service": "scholarswarm"}
