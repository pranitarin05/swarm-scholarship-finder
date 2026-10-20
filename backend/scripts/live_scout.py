"""Live smoke: scout discovers candidates for Rahul (run manually)."""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from backend.app.agents.scout import discover
from backend.app.llm import LLMClient
from backend.app.state import Preferences, RunState, StudentProfile


async def main() -> None:
    state = RunState(
        run_id="live-scout",
        preferences=Preferences(
            countries=["Canada", "Germany", "Singapore"],
            level="Master's",
            budget="fully funded",
        ),
        profile=StudentProfile(
            name="Rahul",
            degree="B.Tech CSE",
            cgpa_pct=85.0,
            skills=["Python", "PyTorch", "ML"],
            interests=["AI/ML"],
        ),
    )
    out = await discover(state, llm=LLMClient())
    candidates = out["candidates"]
    official = sum(1 for c in candidates if c.official_url)
    print(f"TOTAL: {len(candidates)} candidates, official: {official}")
    for c in candidates[:15]:
        mark = "OK" if c.official_url else "--"
        print(f"[{mark}] [{c.country or '?'}] {c.name[:65]} -> {c.url[:85]}")


if __name__ == "__main__":
    asyncio.run(main())
