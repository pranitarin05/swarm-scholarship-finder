"""FastAPI app: run creation, SSE event stream, result retrieval."""
from __future__ import annotations

import asyncio
import json
import os
import uuid
from pathlib import Path
from typing import AsyncIterator, Optional

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from .config import ROOT_DIR, settings
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

FRONTEND_DIR = ROOT_DIR / "frontend"


def _parse_json_form(value: str, fallback):
    if not value:
        return fallback
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return fallback


async def _build_run(
    resume: Optional[UploadFile],
    marksheet: Optional[UploadFile],
    preferences: str,
    demo_mode: bool,
    inject_failure: str,
) -> tuple[str, Optional[RunState]]:
    """Create run folder + state. Shared by POST /api/runs and /api/runs/stream."""
    if demo_mode:
        run_id = "demo-" + uuid.uuid4().hex[:8]
        run_store.create(run_id)
        return run_id, None

    run_id = uuid.uuid4().hex[:12]
    ctx = run_store.create(run_id)
    run_dir = ctx.run_dir()
    prefs_raw = _parse_json_form(preferences, {})
    prefs = Preferences.model_validate(prefs_raw) if isinstance(prefs_raw, dict) else Preferences()

    resume_path = run_dir / Path(resume.filename or "resume.pdf").name
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
    (run_dir / "state.json").write_text(state.model_dump_json(), encoding="utf-8")
    return run_id, state


@app.post("/api/runs")
async def create_run(
    resume: UploadFile = File(...),
    marksheet: Optional[UploadFile] = File(None),
    preferences: str = Form("{}"),
    demo_mode: bool = Form(False),
    inject_failure: str = Form(""),
) -> dict:
    """Store uploads, create a run, start the agent graph in the background."""
    run_id, state = await _build_run(resume, marksheet, preferences, demo_mode, inject_failure)
    if state is None:
        asyncio.create_task(_replay_demo(run_id))
        return {"run_id": run_id, "demo_mode": True}
    asyncio.create_task(_execute(run_id, state, inject_fail=inject_failure))
    return {"run_id": run_id}


@app.post("/api/runs/stream")
async def create_run_stream(
    resume: Optional[UploadFile] = File(None),
    marksheet: Optional[UploadFile] = File(None),
    preferences: str = Form("{}"),
    demo_mode: bool = Form(False),
    inject_failure: str = Form(""),
) -> StreamingResponse:
    """Single-invocation SSE run: create + execute + stream in ONE function call.

    Serverless instances share neither memory nor disk, so the run must be
    created, executed and streamed inside the same invocation. The client gets
    `run_created`, then live agent events, then `stream_end` carrying the final
    result — no follow-up request needed (which would hit another instance).
    """
    if not demo_mode and resume is None:
        raise HTTPException(status_code=422, detail="resume PDF required unless demo_mode")
    run_id, state = await _build_run(resume, marksheet, preferences, demo_mode, inject_failure)
    if state is None:
        task: asyncio.Task = asyncio.create_task(_replay_demo(run_id))
    else:
        task = asyncio.create_task(_execute(run_id, state, inject_fail=inject_failure))
    return StreamingResponse(
        _run_stream(run_id, task),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


async def _run_stream(run_id: str, task: asyncio.Task) -> AsyncIterator[str]:
    """SSE generator for /api/runs/stream: run_created, events, heartbeat, result."""
    ctx = run_store.get(run_id)
    yield _sse({"type": "run_created", "run_id": run_id, "demo_mode": run_id.startswith("demo-")})
    getter: asyncio.Task = asyncio.ensure_future(ctx.queue.get())
    try:
        while True:
            done, _ = await asyncio.wait({getter}, timeout=15.0)
            if not done:
                yield ": ping\n\n"  # heartbeat keeps proxies from idling out
                continue
            event = getter.result()
            if event is None:
                break
            getter = asyncio.ensure_future(ctx.queue.get())
            yield _sse(event.model_dump())
    finally:
        if not getter.done():
            getter.cancel()
        if not task.done():
            task.cancel()
            try:
                await task
            except BaseException:  # noqa: BLE001 - cancelled or crashed pipeline
                pass
    yield _sse({"type": "stream_end", "status": ctx.status, "result": ctx.result or {}})


def _sse(payload: dict) -> str:
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


async def _execute(run_id: str, state: RunState, inject_fail: str = "") -> None:
    """Run the graph; always mark the run finished so SSE can close."""
    timeout = settings.run_timeout
    try:
        if timeout:
            final = await asyncio.wait_for(
                run_pipeline(state, inject_fail=inject_fail), timeout=timeout
            )
        else:
            final = await run_pipeline(state, inject_fail=inject_fail)
        result = final.model_dump(mode="json")
        emit_event(run_id, "supervisor", "done", "Run complete", {"status": "done"})
        run_store.set_result(run_id, result)
        run_store.set_status(run_id, "done")
    except asyncio.TimeoutError:
        emit_event(
            run_id, "supervisor", "error",
            f"Run aborted after {int(timeout)}s time budget (serverless limit)",
        )
        emit_event(run_id, "supervisor", "done", "Run ended with errors")
        run_store.set_result(run_id, {"status": "failed", "error": "run_timeout"})
        run_store.set_status(run_id, "failed")
    except Exception as exc:
        emit_event(
            run_id, "supervisor", "error",
            f"Run failed: {type(exc).__name__}: {exc}",
        )
        emit_event(run_id, "supervisor", "done", "Run ended with errors")
        run_store.set_result(run_id, {"status": "failed", "error": str(exc)})
        run_store.set_status(run_id, "failed")


async def _replay_demo(run_id: str) -> None:
    """Demo mode: replay cached events with realistic pacing."""
    events_path = settings.demo_cache_dir / "events.json"
    result_path = settings.demo_cache_dir / "result.json"
    if not events_path.exists() or not result_path.exists():
        emit_event(run_id, "supervisor", "error", "Demo cache missing")
        emit_event(run_id, "supervisor", "done", "Demo unavailable")
        run_store.set_status(run_id, "failed")
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
        await asyncio.sleep(settings.demo_event_delay)
    emit_event(run_id, "supervisor", "done", "Demo run complete")
    run_store.set_result(run_id, result)
    run_store.set_status(run_id, "done")


@app.get("/api/sample/files/{filename}")
async def get_sample_file(filename: str):
    """Serve sample PDF files for instant 1-click testing."""
    sample_dir = ROOT_DIR / "data" / "sample_rahul"
    path = (sample_dir / filename).resolve()
    if not path.is_relative_to(sample_dir) or not path.exists():
        raise HTTPException(status_code=404, detail="Sample file not found")
    return FileResponse(path, media_type="application/pdf", filename=filename)


@app.get("/api/sample/profile")
async def get_sample_profile() -> dict:
    """Return default profile fields for the demo persona (Rahul)."""
    return {
        "name": "Rahul Sharma",
        "countries": "Canada, Germany, Singapore",
        "level": "Master's",
        "budget": "fully funded",
        "github_url": "https://github.com/rahul-ai-scholar",
        "extra_links": "https://scholar.google.com/citations?user=sample, https://kaggle.com/rahulsharma",
    }


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


@app.post("/api/runs/{run_id}/inject-failure")
async def inject_failure(run_id: str, payload: dict) -> dict:
    """Arm a deliberate failure for the demo: search_down|page_404|bad_json."""
    mode = payload.get("mode", "")
    allowed = {"", "search_down", "page_404", "page_timeout", "github_404", "bad_json"}
    if mode not in allowed:
        raise HTTPException(status_code=400, detail=f"mode must be one of {allowed}")
    ctx = run_store.get(run_id)
    if ctx is None:
        raise HTTPException(status_code=404, detail="run not found")
    ctx.failure_mode = mode
    if mode:
        emit_event(run_id, "supervisor", "fallback", f"Failure injection armed: {mode}")
    return {"run_id": run_id, "inject_failure": mode}


@app.post("/api/runs/{run_id}/sop")
async def regenerate_sop(run_id: str, payload: dict) -> dict:
    """Regenerate the SOP for a chosen opportunity (candidate_id in body).

    Accepts an optional `state` (the run result) so the request still works on
    serverless, where the live run context may live in another instance.
    """
    ctx = run_store.get(run_id)
    state_dict = (ctx.result if ctx and ctx.result else None) or payload.get("state")
    if not state_dict:
        raise HTTPException(status_code=404, detail="run not found or not complete")
    candidate_id = payload.get("candidate_id", "")
    from .agents.writer import write_sop
    from .llm import LLMClient

    state = RunState.model_validate(state_dict)
    emit_event(run_id, "writer", "start", f"Regenerating SOP for {candidate_id}")
    output = await write_sop(state, candidate_id=candidate_id, llm=LLMClient())
    if output.get("sop"):
        sop = output["sop"].model_dump(mode="json")
        if ctx and ctx.result:
            ctx.result["sop"] = sop
            run_store.set_result(run_id, ctx.result)
        emit_event(run_id, "writer", "done", "SOP regenerated")
        return {"sop": sop}
    raise HTTPException(status_code=500, detail="SOP generation failed")


def _render_markdown_report(result: dict) -> str:
    """Build a Markdown report from the run result."""
    lines = ["# ScholarSwarm Report", ""]
    profile = result.get("profile", {})
    lines += [
        f"**Student:** {profile.get('name', 'Unknown')} — "
        f"{profile.get('degree', '')} ({profile.get('cgpa_pct', '?')}%)",
        f"**Targets:** {', '.join(result.get('preferences', {}).get('countries', []))}",
        "",
        "## Top 5 Recommendations",
        "",
    ]
    candidates = {c["id"]: c for c in result.get("candidates", [])}
    reports = {r["candidate_id"]: r for r in result.get("reports", [])}
    for i, ranked in enumerate(result.get("ranked", []), 1):
        cand = candidates.get(ranked["candidate_id"], {})
        rep = reports.get(ranked["candidate_id"], {})
        lines += [
            f"### {i}. {cand.get('name', ranked.get('candidate_name', ''))}",
            f"- Score: **{ranked.get('total_score', 0)}**/100 "
            f"`{json.dumps(ranked.get('breakdown', {}))}`",
            f"- URL: {cand.get('url', rep.get('official_url', ''))}",
            f"- Why: {ranked.get('why_fits', '')}",
        ]
        for label in ("funding", "deadline", "min_grade", "degree_req",
                      "english_req", "how_to_apply"):
            f = rep.get(label, {})
            if f.get("value"):
                badge = "verified" if f.get("confidence") not in (None, "unverified") else "UNVERIFIED"
                quote = f.get("source_quote", "")
                lines.append(
                    f"- {label}: {f['value']} [{badge}]"
                    + (f" — \"{quote[:120]}\"" if quote else "")
                    + (f" ({f.get('source_url', '')})" if f.get("source_url") else "")
                )
        for gap in rep.get("unmet", []):
            lines.append(f"- ⚠ Unmet: {gap}")
        for unk in rep.get("unknown", [])[:4]:
            lines.append(f"- ? Unknown: {unk}")
        lines.append("")
    sop = result.get("sop") or {}
    if sop.get("sop_markdown"):
        lines += ["## Statement of Purpose", "", sop["sop_markdown"], ""]
    warnings = result.get("warnings", [])
    if warnings:
        lines += ["## Warnings"] + [f"- {w}" for w in warnings] + [""]
    lines += [
        "---",
        "*Generated by ScholarSwarm. Every fact above links to its official source "
        "or is marked UNVERIFIED. Always double-check on the official page before applying.*",
    ]
    return "\n".join(lines)


@app.get("/api/runs/{run_id}/export")
async def export_report(run_id: str) -> StreamingResponse:
    """Download the final report as Markdown."""
    ctx = run_store.get(run_id)
    result = ctx.result if ctx else {}
    if not result:
        disk = settings.runs_dir / run_id / "result.json"
        if disk.exists():
            result = json.loads(disk.read_text(encoding="utf-8"))
    if not result:
        raise HTTPException(status_code=404, detail="run not found or incomplete")
    return _markdown_download(run_id, result)


@app.post("/api/runs/{run_id}/export")
async def export_report_stateless(run_id: str, payload: dict) -> StreamingResponse:
    """Download the report as Markdown from a result passed in the request body.

    Serverless fallback: the client already holds the result in memory, so the
    export never depends on server-side run storage.
    """
    ctx = run_store.get(run_id)
    result = payload.get("state") or (ctx.result if ctx else {}) or {}
    if not result:
        raise HTTPException(status_code=404, detail="no result provided")
    return _markdown_download(run_id, result)


def _markdown_download(run_id: str, result: dict) -> StreamingResponse:
    md = _render_markdown_report(result)
    return StreamingResponse(
        iter([md]),
        media_type="text/markdown",
        headers={"Content-Disposition": f'attachment; filename="scholarswarm_{run_id}.md"'},
    )


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(FRONTEND_DIR / "index.html")


if FRONTEND_DIR.exists():
    app.mount("/", StaticFiles(directory=str(FRONTEND_DIR), html=True), name="frontend")
