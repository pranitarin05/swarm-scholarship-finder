"""Record a real pipeline run into data/demo_cache/ for offline demo replay.

Usage: python backend/scripts/record_demo.py
Produces: data/demo_cache/events.json, data/demo_cache/result.json
"""
from __future__ import annotations

import asyncio
import json
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from backend.app.config import settings
from backend.app.events import Event, run_store
from backend.app.graph import run_pipeline
from backend.app.state import Preferences, RunState


async def main() -> None:
    run_id = "demo-record"
    ctx = run_store.create(run_id)
    state = RunState(
        run_id=run_id,
        preferences=Preferences(
            countries=["Canada", "Germany", "Singapore"],
            level="Master's",
            budget="fully funded (tuition waiver + stipend preferred)",
            interests=["AI/ML"],
        ),
        resume_path=str(settings.sample_dir / "resume.pdf"),
        marksheet_path=str(settings.sample_dir / "marksheet.pdf"),
        github_url="https://github.com/rahul-sharma-ai",
        extra_links=[],
    )
    final = await run_pipeline(state)
    result = final.model_dump(mode="json")

    events = [e.model_dump(mode="json") for e in ctx.events]
    # strip absolute paths / ids that should not leak into the replay
    for event in events:
        event.pop("run_id", None)
    result.pop("resume_path", None)
    result.pop("marksheet_path", None)

    settings.demo_cache_dir.mkdir(parents=True, exist_ok=True)
    (settings.demo_cache_dir / "events.json").write_text(
        json.dumps(events, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (settings.demo_cache_dir / "result.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    top = result.get("ranked", [])
    print(f"demo recorded: {len(events)} events, {len(result.get('candidates', []))} "
          f"candidates, top5 scores {[r['total_score'] for r in top]}")


if __name__ == "__main__":
    asyncio.run(main())
