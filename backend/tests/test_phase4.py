"""Phase 4 tests: writer evidence pool, critic rejects planted fake deadline,
failure injection recovery."""
from __future__ import annotations

import asyncio

from backend.app.agents.critic import audit
from backend.app.agents.writer import build_evidence_pool
from backend.app.llm import (
    FAILURE_INJECTION,
    ChatMessage,
    LLMClient,
    MockProvider,
    Provider,
    set_failure_injection,
)
from backend.app.state import (
    Audit,
    Confidence,
    EligibilityReport,
    EnrichedProfile,
    Evidence,
    GithubInfo,
    Preferences,
    Project,
    Ranked,
    RunState,
    SourcedField,
    SOPDraft,
    StudentProfile,
)


def test_evidence_pool_includes_projects_and_github():
    profile = StudentProfile(
        name="Rahul",
        degree="B.Tech CSE",
        institution="LPU",
        cgpa_raw="8.2",
        cgpa_scale="10",
        projects=[Project(name="Crop Disease Detector", summary="CNN", tech=["PyTorch"])],
    )
    profile.normalize_cgpa()
    enriched = EnrichedProfile(
        github=GithubInfo(username="rahul", profile_url="https://github.com/rahul"),
        evidence=[Evidence(claim="GitHub: 3 repos", source_url="https://github.com/rahul")],
    )
    pool = build_evidence_pool(profile, enriched)
    assert any("Crop Disease Detector" in e.claim for e in pool)
    assert any("github.com/rahul" in e.source_url for e in pool)
    assert any(e.source_url == "marksheet" for e in pool)


def test_critic_rejects_planted_fake_deadline(monkeypatch):
    """Plant a deadline whose quote does NOT appear in the re-fetched page."""

    class FakePage:
        ok = True
        text = "The scholarship covers full tuition. Applications are welcome."
        note = ""

    async def fake_fetch(url, inject_fail=""):
        return FakePage()

    monkeypatch.setattr("backend.app.agents.critic.fetch_page", fake_fetch)

    report = EligibilityReport(candidate_id="fake-scholarship", status="verified")
    report.funding = SourcedField(
        value="full tuition",
        source_url="https://official.edu/scholarship",
        source_quote="The scholarship covers full tuition",
        confidence=Confidence.high,
    )
    report.deadline = SourcedField(
        value="15 March 2026",
        source_url="https://official.edu/scholarship",
        source_quote="Applications close 15 March 2026",  # NOT in page
        confidence=Confidence.high,
    )
    state = RunState(
        run_id="critic-test",
        reports=[report],
        profile=StudentProfile(name="Rahul"),
        preferences=Preferences(countries=["Germany"]),
    )
    output = asyncio.run(audit(state, llm=LLMClient(providers=[MockProvider()])))
    audit_obj: Audit = output["audit"]
    assert audit_obj.verdict == "revise"
    fails = [f for f in audit_obj.findings if f.verdict == "fail"]
    assert any("deadline" in f.claim for f in fails)
    # the planted field must have been downgraded
    assert report.deadline.confidence == Confidence.unverified
    # the genuine quote must have passed
    assert any(f.verdict == "pass" and "funding" in f.claim for f in audit_obj.findings)


def test_critic_passes_when_quotes_match(monkeypatch):
    class FakePage:
        ok = True
        text = "Applications close 15 March 2026. Full tuition waived."
        note = ""

    async def fake_fetch(url, inject_fail=""):
        return FakePage()

    monkeypatch.setattr("backend.app.agents.critic.fetch_page", fake_fetch)
    report = EligibilityReport(candidate_id="ok-sch", status="verified")
    report.deadline = SourcedField(
        value="15 March 2026", source_url="https://official.edu/x",
        source_quote="Applications close 15 March 2026", confidence=Confidence.high,
    )
    state = RunState(run_id="critic-pass", reports=[report], profile=StudentProfile(name="R"))
    output = asyncio.run(audit(state, llm=LLMClient(providers=[MockProvider()])))
    assert output["audit"].verdict == "pass"
    assert all(f.verdict == "pass" for f in output["audit"].findings)


def test_bad_json_injection_triggers_repair_then_succeeds():
    """Injecting bad_json must be consumed by the repair retry, not crash."""
    calls = {"n": 0}

    class GoodThenNormal(Provider):
        name = "test"

        async def chat(self, messages, temperature=0.2):
            calls["n"] += 1
            return '{"name": "Recovered", "cgpa_raw": "8.0", "cgpa_scale": "10"}'

    set_failure_injection("bad_json")
    client = LLMClient(providers=[GoodThenNormal()])
    profile = asyncio.run(client.structured("extract", StudentProfile))
    assert client.last_provider == "test"
    assert FAILURE_INJECTION.get("mode") == ""  # consumed
    assert calls["n"] >= 1


def test_sop_regeneration_uses_state():
    """The writer's evidence map must reference only given evidence."""
    profile = StudentProfile(
        name="Rahul",
        projects=[Project(name="RAG Assistant", summary="RAG chatbot")],
    )
    enriched = EnrichedProfile(evidence=[])
    pool = build_evidence_pool(profile, enriched)
    assert all(e.claim for e in pool)
    assert any("RAG Assistant" in e.claim for e in pool)
