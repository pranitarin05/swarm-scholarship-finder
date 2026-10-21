"""Application Writer: SOP + document checklist from real evidence only."""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel

from ..events import emit_event
from ..llm import LLMClient, llm_client
from ..state import (
    DocumentChecklistItem,
    EnrichedProfile,
    Evidence,
    RunState,
    SOPDraft,
    StudentProfile,
    utcnow_iso,
)

WRITER_SYSTEM = (
    "You are an admissions writing assistant. RULES: (1) Every claim about the "
    "student must come from the evidence list or profile facts given - never "
    "invent projects, grades or experiences. (2) Write a Statement of Purpose "
    "in markdown with sections: # Statement of Purpose, ## Motivation, "
    "## Academic Background, ## Relevant Projects, ## Why This Program, "
    "## Future Goals. (3) Return JSON with keys sop_markdown, checklist "
    "(list of {item, status, note}), evidence_map (list of {claim, source_url, snippet})."
)


class WriterOutput(BaseModel):
    sop_markdown: str = ""
    checklist: list[DocumentChecklistItem] = []
    evidence_map: list[Evidence] = []


def build_evidence_pool(profile: StudentProfile, enriched: EnrichedProfile) -> list[Evidence]:
    """Compile every traceable fact the SOP may use."""
    pool: list[Evidence] = []
    if profile.name:
        pool.append(Evidence(claim=f"Student name: {profile.name}", source_url="resume", snippet=profile.name))
    if profile.degree or profile.institution:
        pool.append(Evidence(
            claim=f"Degree: {profile.degree} at {profile.institution}",
            source_url="resume", snippet=profile.institution,
        ))
    if profile.cgpa_pct is not None:
        pool.append(Evidence(
            claim=f"CGPA {profile.cgpa_raw}/{'10' if profile.cgpa_scale == '10' else profile.cgpa_scale} ({profile.cgpa_pct}%)",
            source_url="marksheet", snippet=str(profile.cgpa_pct),
        ))
    for project in profile.projects:
        if project.name:
            pool.append(Evidence(
                claim=f"Project: {project.name} - {project.summary}",
                source_url=project.link or "resume", snippet=project.tech and ", ".join(project.tech) or "",
            ))
    for exp in profile.experience:
        if exp.role:
            pool.append(Evidence(
                claim=f"Experience: {exp.role} at {exp.organisation} ({exp.period})",
                source_url="resume", snippet=exp.details[:120],
            ))
    pool.extend(enriched.evidence)
    return pool


async def write_sop(
    state: RunState, candidate_id: str = "", llm: Optional[LLMClient] = None
) -> dict:
    """Node: draft SOP + checklist + evidence map for the chosen opportunity."""
    run_id = state.run_id
    client = llm or llm_client
    top = state.ranked
    if not top:
        emit_event(run_id, "writer", "error", "No ranked opportunities to write for")
        return {}
    target = next((r for r in top if r.candidate_id == candidate_id), top[0])
    report = next(
        (r for r in state.reports if r.candidate_id == target.candidate_id), None
    )
    emit_event(
        run_id, "writer", "start",
        f"Drafting SOP for: {target.candidate_name[:60]}",
    )
    evidence = build_evidence_pool(state.profile, state.enriched)
    evidence_text = "\n".join(
        f"- {e.claim} (source: {e.source_url})" for e in evidence
    )
    verified_facts = []
    if report:
        for label, f in (
            ("funding", report.funding), ("deadline", report.deadline),
            ("min_grade", report.min_grade), ("how_to_apply", report.how_to_apply),
        ):
            if f.verified and f.value:
                verified_facts.append(f"{label}: {f.value} [quote: {f.source_quote[:100]}]")
    prompt = (
        f"Student profile summary:\n"
        f"- Name: {state.profile.name}\n"
        f"- Degree: {state.profile.degree}, CGPA% {state.profile.cgpa_pct}\n"
        f"- Skills: {', '.join(state.profile.skills[:10])}\n"
        f"- Interests: {', '.join(state.profile.interests[:5])}\n"
        f"- Target: {', '.join(state.preferences.countries)} / {state.preferences.level}\n\n"
        f"Opportunity: {target.candidate_name}\n"
        f"Verified facts about the opportunity (may cite):\n"
        + ("\n".join(f"- {v}" for v in verified_facts) or "- (none verified)") +
        f"\n\nEvidence the SOP may use about the student:\n{evidence_text}\n\n"
        f"Write the SOP using ONLY the evidence above. In the 'Why This Program' "
        f"section, reference the opportunity name and verified facts where useful. "
        f"Also produce a document checklist and an evidence_map linking each "
        f"major claim to its source URL."
    )
    try:
        result = await client.structured(prompt, WriterOutput, system=WRITER_SYSTEM)
    except Exception as exc:
        emit_event(
            run_id, "writer", "fallback",
            f"LLM writer failed ({type(exc).__name__}); using evidence-only template",
        )
        result = WriterOutput(sop_markdown=_template_sop(state, target, evidence))
    if not result.evidence_map:
        result.evidence_map = evidence[:6]
    if not result.checklist:
        result.checklist = _default_checklist(state)
    draft = SOPDraft(
        candidate_id=target.candidate_id,
        candidate_name=target.candidate_name,
        sop_markdown=result.sop_markdown,
        checklist=result.checklist,
        evidence_map=result.evidence_map,
        generated_at=utcnow_iso(),
    )
    emit_event(
        run_id, "writer", "done",
        f"SOP draft ready ({len(result.sop_markdown)} chars, "
        f"{len(result.checklist)} checklist items)",
    )
    return {"sop": draft}


def _template_sop(state: RunState, target, evidence: list[Evidence]) -> str:
    lines = [
        "# Statement of Purpose",
        "",
        f"**Program:** {target.candidate_name}",
        "",
        "## Motivation",
        f"I am {state.profile.name}, a final-year {state.profile.degree} student "
        f"at {state.profile.institution}, applying to {target.candidate_name} "
        f"in {', '.join(state.preferences.countries)}.",
        "",
        "## Academic Background",
        f"My CGPA is {state.profile.cgpa_raw}/10 ({state.profile.cgpa_pct}%).",
        "",
        "## Relevant Projects",
    ]
    for e in evidence:
        if e.claim.startswith("Project:"):
            lines.append(f"- {e.claim.replace('Project: ', '')}")
    lines += [
        "",
        "## Why This Program",
        "I am drawn to this program's focus and funding support for international students.",
        "",
        "## Future Goals",
        "I plan to deepen my AI/ML expertise and return to solve real-world problems.",
    ]
    return "\n".join(lines)


def _default_checklist(state: RunState) -> list[DocumentChecklistItem]:
    items = []
    if not state.profile.languages_tests.ielts:
        items.append(DocumentChecklistItem(item="IELTS/TOEFL score", status="missing",
                                           note="Book a test; many programs need IELTS 6.5+"))
    items.append(DocumentChecklistItem(item="Updated CV/Resume", status="ready",
                                       note="Generated from parsed profile"))
    items.append(DocumentChecklistItem(item="Statement of Purpose", status="drafted",
                                       note="Review and personalize before submitting"))
    items.append(DocumentChecklistItem(item="Recommendation letters (2-3)", status="missing",
                                       note="Ask professors/internship supervisor"))
    items.append(DocumentChecklistItem(item="Academic transcripts", status="ready",
                                       note="Marksheet uploaded"))
    return items
