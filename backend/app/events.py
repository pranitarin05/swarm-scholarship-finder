"""Per-run event bus: every agent step becomes an Event on the SSE timeline."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any, AsyncIterator, Optional

from .config import settings
from .state import Event

EVENT_TYPES = {
    "start", "tool_call", "result", "retry",
    "fallback", "handoff", "error", "done",
}


class RunContext:
    """Holds the live queue + accumulated history for one run."""

    def __init__(self, run_id: str) -> None:
        self.run_id = run_id
        self.queue: asyncio.Queue[Optional[Event]] = asyncio.Queue()
        self.events: list[Event] = []
        self.status = "created"
        self.result: dict[str, Any] = {}
        self.failure_mode: str = ""
        self.closed: bool = False

    def close_queue(self) -> None:
        if not self.closed:
            self.closed = True
            self.queue.put_nowait(None)

    def run_dir(self) -> Path:
        d = settings.runs_dir / self.run_id
        d.mkdir(parents=True, exist_ok=True)
        return d

    def persist_event(self, event: Event) -> None:
        path = self.run_dir() / "events.jsonl"
        with path.open("a", encoding="utf-8") as f:
            f.write(event.model_dump_json() + "\n")


class RunStore:
    """In-memory registry of runs; history is also written to disk as JSONL."""

    def __init__(self) -> None:
        self._runs: dict[str, RunContext] = {}

    def create(self, run_id: str) -> RunContext:
        ctx = RunContext(run_id)
        self._runs[run_id] = ctx
        return ctx

    def get(self, run_id: str) -> Optional[RunContext]:
        return self._runs.get(run_id)

    def emit(
        self,
        run_id: str,
        agent: str,
        type: str,
        message: str,
        data: Optional[dict[str, Any]] = None,
    ) -> Event:
        """Append one timeline event and push it to any live SSE listeners."""
        ctx = self._runs.get(run_id)
        if ctx is None:
            ctx = self.create(run_id)
        if type not in EVENT_TYPES:
            type = "result"
        event = Event(
            run_id=run_id, agent=agent, type=type,
            message=message, data=data or {},
        )
        ctx.events.append(event)
        try:
            ctx.persist_event(event)
        except OSError:
            pass
        if not ctx.closed:
            ctx.queue.put_nowait(event)
        return event

    def set_status(self, run_id: str, status: str) -> None:
        ctx = self._runs.get(run_id)
        if ctx:
            ctx.status = status
            if status in ("done", "failed"):
                ctx.close_queue()

    def set_result(self, run_id: str, result: dict[str, Any]) -> None:
        ctx = self._runs.get(run_id)
        if ctx:
            ctx.result = result
            ctx.status = "done"
            try:
                path = ctx.run_dir() / "result.json"
                path.write_text(
                    json.dumps(result, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
            except OSError:
                pass
            ctx.close_queue()

    async def stream(self, run_id: str) -> AsyncIterator[str]:
        """SSE generator: replay history, then forward live events until done."""
        ctx = self._runs.get(run_id)
        if ctx is None:
            yield _sse({"type": "error", "message": "unknown run"})
            return
        for event in list(ctx.events):
            yield _sse(event.model_dump())
        if ctx.status in ("done", "failed") and ctx.queue.empty():
            yield _sse({"type": "stream_end", "status": ctx.status})
            return
        while True:
            event = await ctx.queue.get()
            if event is None:
                yield _sse({"type": "stream_end", "status": ctx.status})
                return
            yield _sse(event.model_dump())

    def load_replay(self, run_id: str, events: list[dict[str, Any]],
                    result: dict[str, Any]) -> RunContext:
        """Demo mode: load a recorded run into memory for SSE replay."""
        ctx = self.create(run_id)
        for raw in events:
            ctx.events.append(Event.model_validate(raw))
        ctx.result = result
        ctx.status = "done"
        return ctx


def _sse(payload: dict[str, Any]) -> str:
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


run_store = RunStore()


def emit_event(
    run_id: str,
    agent: str,
    type: str,
    message: str,
    data: Optional[dict[str, Any]] = None,
) -> Event:
    """Module-level helper used inside every agent."""
    return run_store.emit(run_id, agent, type, message, data)
