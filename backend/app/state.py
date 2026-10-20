"""Typed shared state for the agent graph (Pydantic v2).

Every fact that will be shown to the student is a SourcedField carrying its
source URL, a verbatim quote and a confidence label. No quote -> unverified.
"""
from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class Confidence(str, Enum):
    high = "high"
    medium = "medium"
    low = "low"
    unverified = "unverified"


class SourcedField(BaseModel):
    """A single extracted fact with provenance. The trust backbone."""

    value: str = ""
    source_url: str = ""
    source_quote: str = ""
    fetched_at: str = ""
    confidence: Confidence = Confidence.unverified

    @property
    def verified(self) -> bool:
        return (
            self.confidence != Confidence.unverified
            and bool(self.source_url)
            and bool(self.source_quote)
        )


class Project(BaseModel):
    name: str = ""
    summary: str = ""
    tech: list[str] = []
    link: str = ""


class Experience(BaseModel):
    role: str = ""
    organisation: str = ""
    period: str = ""
    details: str = ""


class Publication(BaseModel):
    title: str = ""
    venue: str = ""
    year: str = ""
    link: str = ""


class LanguageTests(BaseModel):
    ielts: str = ""
    toefl: str = ""
    gre: str = ""


class StudentProfile(BaseModel):
    """Output of the Profile Analyst."""

    name: str = ""
    email: str = ""
    degree: str = ""
    institution: str = ""
    graduation_year: str = ""
    cgpa_raw: str = ""
    cgpa_scale: str = ""
    cgpa_pct: Optional[float] = None
    cgpa_4_0: Optional[float] = None
    target_countries: list[str] = []
    target_level: str = "Master's"
    budget: str = ""
    interests: list[str] = []
    skills: list[str] = []
    projects: list[Project] = []
    experience: list[Experience] = []
    publications: list[Publication] = []
    languages_tests: LanguageTests = LanguageTests()
    statement: str = ""
    missing_fields: list[str] = []

    def normalize_cgpa(self) -> None:
        """Fill cgpa_pct / cgpa_4_0 from cgpa_raw + cgpa_scale; never guesses scale."""
        try:
            raw = float(self.cgpa_raw)
        except (TypeError, ValueError):
            return
        scale = (self.cgpa_scale or "").strip().lower()
        pct: Optional[float] = None
        if scale.startswith("10"):
            pct = round(raw * 10.0, 1)
        elif scale.startswith("4"):
            pct = round(raw * 25.0, 1)
        elif scale.startswith("100") or "%" in scale:
            pct = raw
        else:
            return
        self.cgpa_pct = pct
        self.cgpa_4_0 = round(min(4.0, pct / 25.0), 2)


class GithubRepo(BaseModel):
    name: str = ""
    description: str = ""
    language: str = ""
    stars: int = 0
    fork: bool = False
    url: str = ""
    updated_at: str = ""
    topics: list[str] = []


class GithubInfo(BaseModel):
    username: str = ""
    profile_url: str = ""
    repos: list[GithubRepo] = []
    top_languages: list[str] = []
    total_stars: int = 0
    public_repos: int = 0
    activity: str = ""
    warnings: list[str] = []


class Evidence(BaseModel):
    claim: str = ""
    source_url: str = ""
    snippet: str = ""


class EnrichedProfile(BaseModel):
    """Output of the Profile Enricher."""

    github: GithubInfo = GithubInfo()
    extra_links: list[str] = []
    evidence: list[Evidence] = []
    strengths: list[str] = []
    gaps: list[str] = []
    unverified_resume_claims: list[str] = []


class Candidate(BaseModel):
    """A discovered opportunity, before verification."""

    id: str = ""
    name: str = ""
    institution: str = ""
    country: str = ""
    level: str = ""
    type: str = ""
    url: str = ""
    official_url: str = ""
    found_via_query: str = ""
    discovered_via: str = ""


class EligibilityReport(BaseModel):
    """Output of the Eligibility Verifier for one candidate."""

    candidate_id: str = ""
    official_url: str = ""
    degree_req: SourcedField = SourcedField()
    min_grade: SourcedField = SourcedField()
    english_req: SourcedField = SourcedField()
    documents: SourcedField = SourcedField()
    deadline: SourcedField = SourcedField()
    funding: SourcedField = SourcedField()
    how_to_apply: SourcedField = SourcedField()
    met: list[str] = []
    unmet: list[str] = []
    unknown: list[str] = []
    status: str = "failed"
    notes: str = ""

    @property
    def verified_share(self) -> float:
        fields = [
            self.degree_req, self.min_grade, self.english_req,
            self.documents, self.deadline, self.funding, self.how_to_apply,
        ]
        if not fields:
            return 0.0
        return sum(1 for f in fields if f.verified) / len(fields)


class Ranked(BaseModel):
    """Output of the Matcher/Ranker for one candidate."""

    candidate_id: str = ""
    candidate_name: str = ""
    total_score: float = 0.0
    breakdown: dict[str, float] = {}
    why_fits: str = ""


class DocumentChecklistItem(BaseModel):
    item: str = ""
    status: str = ""
    note: str = ""


class SOPDraft(BaseModel):
    """Output of the Application Writer."""

    candidate_id: str = ""
    candidate_name: str = ""
    sop_markdown: str = ""
    checklist: list[DocumentChecklistItem] = []
    evidence_map: list[Evidence] = []
    generated_at: str = Field(default_factory=utcnow_iso)


class AuditFinding(BaseModel):
    agent: str = ""
    claim: str = ""
    verdict: str = ""
    note: str = ""


class Audit(BaseModel):
    """Output of the Fact-Checker / Critic."""

    findings: list[AuditFinding] = []
    verdict: str = "pass"
    round: int = 0

    @property
    def rejected(self) -> bool:
        return self.verdict == "revise"


class Event(BaseModel):
    """One line on the live agent timeline."""

    ts: str = Field(default_factory=utcnow_iso)
    run_id: str = ""
    agent: str = ""
    type: str = "result"
    message: str = ""
    data: dict[str, Any] = {}


class Preferences(BaseModel):
    countries: list[str] = []
    level: str = "Master's"
    budget: str = ""
    interests: list[str] = []


class RunPlan(BaseModel):
    steps: list[str] = []
    notes: str = ""


class RunState(BaseModel):
    """The single shared state every agent reads and writes."""

    run_id: str = ""
    status: str = "created"
    preferences: Preferences = Preferences()
    resume_path: str = ""
    marksheet_path: str = ""
    github_url: str = ""
    extra_links: list[str] = []
    profile: StudentProfile = StudentProfile()
    enriched: EnrichedProfile = EnrichedProfile()
    candidates: list[Candidate] = []
    reports: list[EligibilityReport] = []
    ranked: list[Ranked] = []
    sop: Optional[SOPDraft] = None
    audit: Optional[Audit] = None
    plan: RunPlan = RunPlan()
    scout_loops: int = 0
    critic_rounds: int = 0
    inject_fail: str = ""
    errors: list[str] = []
    warnings: list[str] = []
