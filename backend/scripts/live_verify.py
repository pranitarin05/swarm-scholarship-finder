"""Live smoke: verify a few real candidates end-to-end (run manually)."""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from backend.app.agents.verifier import verify_one
from backend.app.llm import LLMClient
from backend.app.state import Candidate, Preferences, RunState, StudentProfile


async def main() -> None:
    state = RunState(
        run_id="live-verify",
        preferences=Preferences(countries=["Germany", "Canada"]),
        profile=StudentProfile(
            name="Rahul", cgpa_pct=85.0,
            languages_tests={"ielts": "", "toefl": "", "gre": ""},
        ),
    )
    candidates = [
        Candidate(id="daad-main", name="DAAD Scholarships Overview",
                  url="https://www.daad.de/en/studying-in-germany/scholarships/daad-scholarships/",
                  official_url="https://www.daad.de/en/studying-in-germany/scholarships/daad-scholarships/"),
        Candidate(id="cgrad", name="Canada Graduate Research Scholarship",
                  url="https://sshrc-crsh.canada.ca/en/funding/opportunities/canada-graduate-research-scholarship-masters",
                  official_url="https://sshrc-crsh.canada.ca/en/funding/opportunities/canada-graduate-research-scholarship-masters"),
    ]
    llm = LLMClient()
    for candidate in candidates:
        report = await verify_one(candidate, state, llm)
        print(f"\n=== {candidate.name} -> {report.status} ===")
        for label in ("funding", "deadline", "min_grade", "degree_req", "english_req"):
            f = getattr(report, label)
            status = "VERIFIED" if f.verified else "unverified"
            print(f"  {label}: [{status}] {f.value[:70]!r}")
        print(f"  met={len(report.met)} unmet={len(report.unmet)} unknown={len(report.unknown)}")


if __name__ == "__main__":
    asyncio.run(main())
