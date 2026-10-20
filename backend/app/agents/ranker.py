"""Matcher & Ranker: deterministic rubric -> top 5 with score breakdown.

Weights: fit 30 / funding 30 / deadline 15 / confidence 15 / risk 10.
The LLM only writes the "why it fits" sentence from verified facts.
"""
from __future__ import annotations

import re
from datetime import date, datetime
from typing import Optional

from pydantic import BaseModel

from ..events import emit_event
from ..llm import LLMClient, llm_client
from ..state import EligibilityReport, Ranked, RunState
from ..agents.verifier import parse_deadline

FIT_WEIGHTS = 30
FUNDING_WEIGHTS = 30
DEADLINE_WEIGHTS = 15
CONFIDENCE_WEIGHTS = 15
RISK_WEIGHTS = 10


class WhyFits(BaseModel):
    explanation: str = ""


def score_funding(report: EligibilityReport) -> float:
    """0-10: full tuition+stipend scores highest (family cannot afford fees)."""
    text = (report.funding.value or "").lower()
    if not text:
        return 0.0
    if any(k in text for k in ("fully funded", "full tuition", "full ride", "waives all")):
        return 10.0
    if any(k in text for k in ("tuition waiver", "full scholarship", "100%")):
        return 9.5
    if "stipend" in text and ("tuition" in text or "fee" in text):
        return 8.5
    if any(k in text for k in ("€", "$", "£", "cad", "usd", "per month", "annual")):
        if "waiver" in text or "tuition" in text:
            return 7.5
        return 6.0
    if "partial" in text:
        return 4.0
    return 3.0


def score_deadline(report: EligibilityReport, today: Optional[date] = None) -> float:
    """0-10: enough time to prepare, not already closed."""
    today = today or date.today()
    value = report.deadline.value or ""
    deadline = parse_deadline(value)
    if not deadline:
        return 3.0 if value else 0.0
    days = (deadline - today).days
    if days < 0:
        return 0.0
    if days < 14:
        return 3.0
    if days < 30:
        return 5.0
    if days < 60:
        return 7.5
    if days < 120:
        return 10.0
    return 9.0


def score_fit(profile, report: EligibilityReport, candidate) -> float:
    """0-10: field match, CGPA threshold, skills."""
    score = 3.0
    pct = profile.cgpa_pct
    grade = report.min_grade
    if grade.verified and grade.value and pct is not None:
        m = re.search(r"(\d{2}(?:\.\d)?)\s*%", grade.value)
        if m and pct >= float(m.group(1)):
            score += 4.0
        elif m:
            score += 1.0
        else:
            score += 2.5
    elif pct and pct >= 80:
        score += 2.5
    level_ok = (candidate.level or "master").lower() in ("master", "masters", "master's", "m.sc", "msc")
    if level_ok:
        score += 1.5
    degree = report.degree_req
    if degree.verified and "bachelor" in (degree.value or "").lower():
        score += 1.5
    if profile.interests and candidate.country:
        for interest in profile.interests:
            if interest.lower() in (candidate.name or "").lower():
                score += 1.0
                break
    return min(10.0, score)


def score_confidence(report: EligibilityReport) -> float:
    """0-10: share of fields verified from official sources."""
    return round(report.verified_share * 10.0, 2)


def score_risk(report: EligibilityReport, profile) -> float:
    """0-10 where 10 = lowest risk. Missing docs/tests lower the score."""
    score = 8.0
    if not profile.languages_tests.ielts and report.english_req.value:
        score -= 3.0
    if not report.documents.value:
        score -= 1.0
    if any("passed" in u.lower() for u in report.unmet):
        score -= 4.0
    if not report.official_url:
        score -= 2.0
    return max(0.0, score)


def rank_candidate(
    candidate, report: Optional[EligibilityReport], profile
) -> Ranked:
    """Compute the full rubric breakdown for one candidate."""
    if report is None:
        report = EligibilityReport(candidate_id=candidate.id)
    fit = round(score_fit(profile, report, candidate), 2)
    funding = round(score_funding(report), 2)
    deadline = round(score_deadline(report), 2)
    confidence = round(score_confidence(report), 2)
    risk = round(score_risk(report, profile), 2)

    breakdown = {
        "fit": round(fit / 10 * FIT_WEIGHTS, 1),
        "funding": round(funding / 10 * FUNDING_WEIGHTS, 1),
        "deadline": round(deadline / 10 * DEADLINE_WEIGHTS, 1),
        "confidence": round(confidence / 10 * CONFIDENCE_WEIGHTS, 1),
        "risk": round(risk / 10 * RISK_WEIGHTS, 1),
    }
    total = round(sum(breakdown.values()), 1)
    return Ranked(
        candidate_id=candidate.id,
        candidate_name=candidate.name,
        total_score=total,
        breakdown=breakdown,
    )


async def write_explanations(
    ranked: list[Ranked], state: RunState, llm: LLMClient
) -> None:
    """LLM writes 'why it fits' using verified facts only."""
    run_id = state.run_id
    reports = {r.candidate_id: r for r in state.reports}
    profile = state.profile
    for item in ranked:
        report = reports.get(item.candidate_id)
        if report is None:
            item.why_fits = "No verified facts available for this opportunity."
            continue
        facts = []
        for label, f in (
            ("funding", report.funding), ("deadline", report.deadline),
            ("min_grade", report.min_grade), ("degree_req", report.degree_req),
        ):
            if f.verified and f.value:
                facts.append(f"{label}: {f.value}")
        if not facts:
            item.why_fits = "Not enough verified facts to justify a recommendation."
            continue
        prompt = (
            f"Student: {profile.name}, CGPA% {profile.cgpa_pct}, skills "
            f"{', '.join(profile.skills[:5])}.\n"
            f"Opportunity: {item.candidate_name}\n"
            f"Verified facts (use ONLY these):\n- " + "\n- ".join(facts) +
            "\n\nWrite 2-3 sentences (<=60 words) explaining why this fits. "
            "Be concrete. Return JSON: {\"explanation\": \"...\"}"
        )
        try:
            result = await llm.structured(prompt, WhyFits, system=(
                "You write concise fit explanations for scholarship "
                "recommendations using only the verified facts given."
            ))
            item.why_fits = result.explanation.strip()
        except Exception:
            item.why_fits = "; ".join(facts)[:300]


async def rank(state: RunState, llm: Optional[LLMClient] = None) -> dict:
    """Node: score all verified candidates, pick top 5, write explanations."""
    run_id = state.run_id
    client = llm or llm_client
    emit_event(
        run_id, "ranker", "start",
        f"Scoring {len(state.candidates)} candidates with rubric "
        f"(fit {FIT_WEIGHTS} / funding {FUNDING_WEIGHTS} / deadline {DEADLINE_WEIGHTS} "
        f"/ confidence {CONFIDENCE_WEIGHTS} / risk {RISK_WEIGHTS})",
    )
    report_by_id = {r.candidate_id: r for r in state.reports}
    scored: list[Ranked] = []
    for candidate in state.candidates:
        report = report_by_id.get(candidate.id)
        if report is None or report.status == "failed":
            continue
        scored.append(rank_candidate(candidate, report, state.profile))
    scored.sort(key=lambda r: -r.total_score)
    top5 = scored[:5]
    emit_event(
        run_id, "ranker", "result",
        f"Top candidate: {top5[0].candidate_name[:60] if top5 else 'none'} "
        f"(score {top5[0].total_score if top5 else 0})",
        {"scores": [r.total_score for r in top5]},
    )
    emit_event(run_id, "ranker", "tool_call", "Writing fit explanations from verified facts")
    await write_explanations(top5, state, client)
    emit_event(
        run_id, "ranker", "done",
        f"Top {len(top5)} selected with score breakdown",
        {"top5": [r.candidate_id for r in top5]},
    )
    return {"ranked": top5}
