"""Phase 1 tests: state models, PDF tools, LLM wrapper, analyst, enricher, API."""
from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
from pydantic import ValidationError

from backend.app.llm import ChatMessage, LLMClient, MockProvider, Provider, register_mock
from backend.app.state import (
    Confidence,
    Preferences,
    RunState,
    SourcedField,
    StudentProfile,
)
from backend.app.tools.github import GithubClient, username_from_url
from backend.app.tools.pdf import extract_pdf_text

SAMPLE_DIR = Path(__file__).resolve().parents[2] / "data" / "sample_rahul"


def test_sourced_field_requires_quote_for_verification():
    unverified = SourcedField(value="15 Mar 2026")
    assert unverified.confidence == Confidence.unverified
    assert not unverified.verified
    verified = SourcedField(
        value="15 Mar 2026",
        source_url="https://example.edu/scholarship",
        source_quote="Applications close 15 March 2026",
        confidence=Confidence.high,
    )
    assert verified.verified


def test_cgpa_normalisation_from_10_scale():
    profile = StudentProfile(cgpa_raw="8.2", cgpa_scale="10")
    profile.normalize_cgpa()
    assert profile.cgpa_pct == 82.0
    assert profile.cgpa_4_0 == 3.28


def test_cgpa_unknown_scale_is_not_guessed():
    profile = StudentProfile(cgpa_raw="8.2", cgpa_scale="")
    profile.normalize_cgpa()
    assert profile.cgpa_pct is None


def test_sample_pdfs_exist_and_extract_text():
    resume = SAMPLE_DIR / "resume.pdf"
    marksheet = SAMPLE_DIR / "marksheet.pdf"
    assert resume.exists() and marksheet.exists()
    result = extract_pdf_text(resume)
    assert result.ok
    assert "Rahul Sharma" in result.text
    assert "CGPA" in result.text
    ms = extract_pdf_text(marksheet)
    assert "Transcript" in ms.text


class FlakyProvider(Provider):
    """Returns broken JSON first, then valid JSON: exercises the repair path."""

    name = "flaky"
    calls = 0

    async def chat(self, messages: list[ChatMessage], temperature: float = 0.2) -> str:
        FlakyProvider.calls += 1
        if FlakyProvider.calls == 1:
            return "{'cgpa_raw': '8.2'"  # truncated + single quotes
        return json.dumps({"cgpa_raw": "8.2", "cgpa_scale": "10", "name": "X"})


def test_llm_structured_repair_retry():
    import asyncio

    FlakyProvider.calls = 0
    client = LLMClient(providers=[FlakyProvider()])
    profile = asyncio.run(client.structured("extract", StudentProfile, system="test"))
    assert profile.cgpa_raw == "8.2"
    assert FlakyProvider.calls == 2


def test_llm_falls_back_to_next_provider_on_garbage():
    class Garbage(Provider):
        name = "garbage"

        async def chat(self, messages, temperature=0.2):
            return "I cannot produce JSON."

    import asyncio

    client = LLMClient(providers=[Garbage(), MockProvider()])
    register_mock("StudentProfile", {"name": "Rahul", "cgpa_raw": "8.2"})
    profile = asyncio.run(client.structured("extract", StudentProfile))
    assert profile.name == "Rahul"
    assert client.last_provider == "mock"


def test_analyst_builds_profile_with_mock_llm(tmp_path):
    import asyncio

    from backend.app.agents.analyst import analyse

    register_mock(
        "StudentProfile",
        {
            "name": "Rahul Sharma",
            "degree": "B.Tech CSE",
            "institution": "LPU Jalandhar",
            "cgpa_raw": "8.2",
            "cgpa_scale": "10",
            "projects": [{"name": "Crop Disease Detector", "summary": "CNN classifier"}],
            "missing_fields": ["IELTS score"],
        },
    )
    state = RunState(
        run_id="test-analyst",
        preferences=Preferences(countries=["Canada"], level="Master's"),
        resume_path=str(SAMPLE_DIR / "resume.pdf"),
        marksheet_path=str(SAMPLE_DIR / "marksheet.pdf"),
    )
    output = asyncio.run(analyse(state, llm=LLMClient(providers=[MockProvider()])))
    profile = output["profile"]
    assert profile.name == "Rahul Sharma"
    assert profile.cgpa_pct == 82.0
    assert profile.target_countries == ["Canada"]
    assert any("IELTS" in f for f in profile.missing_fields)


def _mock_github(username: str) -> dict:
    return {
        "login": username,
        "html_url": f"https://github.com/{username}",
        "public_repos": 3,
        "repos": [
            {
                "name": "crop-disease-detector",
                "description": "CNN for leaf diseases with PyTorch",
                "language": "Python",
                "stargazers_count": 12,
                "fork": False,
                "html_url": f"https://github.com/{username}/crop-disease-detector",
                "updated_at": "2026-01-10T00:00:00Z",
                "topics": ["deep-learning"],
            },
            {
                "name": "rag-college-assistant",
                "description": "RAG chatbot with LlamaIndex",
                "language": "Python",
                "stargazers_count": 7,
                "fork": False,
                "html_url": f"https://github.com/{username}/rag-college-assistant",
                "updated_at": "2025-11-01T00:00:00Z",
                "topics": [],
            },
            {"name": "old-site", "description": "", "language": "HTML",
             "stargazers_count": 0, "fork": True,
             "html_url": f"https://github.com/{username}/old-site",
             "updated_at": "2023-05-01T00:00:00Z", "topics": []},
        ],
    }


def test_enricher_uses_github_api_and_cross_checks(tmp_path):
    import asyncio

    from backend.app.agents.enricher import enrich
    from backend.app.state import Project

    async def fake_get(url, params=None):
        if url.endswith("/users/rahul-sharma-ai"):
            return {"login": "rahul-sharma-ai",
                    "html_url": "https://github.com/rahul-sharma-ai",
                    "public_repos": 3}
        if url.endswith("/repos"):
            return _mock_github("rahul-sharma-ai")["repos"]
        raise httpx.HTTPStatusError(
            "404", request=httpx.Request("GET", url), response=httpx.Response(404)
        )

    client = GithubClient(token="")
    client._get = fake_get  # type: ignore[method-assign]

    state = RunState(
        run_id="test-enricher",
        github_url="https://github.com/rahul-sharma-ai",
        profile=StudentProfile(
            name="Rahul Sharma",
            projects=[Project(name="Crop Disease Detector", summary="CNN")],
        ),
    )
    output = asyncio.run(enrich(state, github_client=client))
    enriched = output["enriched"]
    assert enriched.github.username == "rahul-sharma-ai"
    assert len(enriched.github.repos) == 3
    assert enriched.github.total_stars == 19
    assert any("Crop Disease Detector" in e.claim for e in enriched.evidence)
    assert enriched.strengths


def test_build_evidence_flags_unverifiable_project():
    from backend.app.agents.enricher import build_evidence
    from backend.app.state import GithubInfo, Project

    profile = StudentProfile(projects=[Project(name="Quantum Blockchain Portal")])
    info = GithubInfo(username="")
    evidence, unverified, strengths, gaps = build_evidence(profile, info)
    assert any("Quantum Blockchain Portal" in u for u in unverified)
    assert gaps


def test_github_username_parsing():
    assert username_from_url("https://github.com/rahul-sharma-ai/") == "rahul-sharma-ai"
    assert username_from_url("github.com/x") == "x"
    assert username_from_url("") == ""


def test_run_state_round_trips():
    state = RunState(run_id="abc", preferences=Preferences(countries=["Germany"]))
    clone = RunState.model_validate(json.loads(state.model_dump_json()))
    assert clone.preferences.countries == ["Germany"]
    with pytest.raises(ValidationError):
        RunState.model_validate({**state.model_dump(), "run_id": 123})
