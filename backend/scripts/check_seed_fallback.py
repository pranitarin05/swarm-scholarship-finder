"""Quick check: scout falls back to seeds when all engines are blocked."""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from backend.app.agents.scout import discover
from backend.app.state import Preferences, RunState, StudentProfile


async def main() -> None:
    state = RunState(
        run_id="seed-check",
        preferences=Preferences(
            countries=["Canada", "Germany", "Singapore"],
            level="Master's",
            budget="fully funded",
        ),
        profile=StudentProfile(name="Rahul", cgpa_pct=85.0),
    )
    out = await discover(state)
    print("candidates:", len(out["candidates"]))
    for c in out["candidates"][:10]:
        print(" ", c.name[:60], "->", c.url[:70])


if __name__ == "__main__":
    asyncio.run(main())
