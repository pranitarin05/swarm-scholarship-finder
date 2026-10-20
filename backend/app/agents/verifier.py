"""Eligibility Verifier: fetch official page, extract quoted Fields, compare."""
from __future__ import annotations

import asyncio
import re
from datetime import date, datetime
from typing import Optional

from pydantic import BaseModel

from ..config import settings
from ..events import emit_event
from ..llm import LLMClient, llm_client
from ..state import (
    Confidence,
    EligibilityReport,
    RunState,
    SourcedField,
    utcnow_iso,
)
from ..tools.fetch import fetch_page


class ExtractedFacts(BaseModel):
    """What the LLM must return for one candidate page."""

    degree_req: str = ""
    degree_quote: str = ""
    min_grade: str = ""
    min_grade_quote: str = ""
    english_req: str = ""
    english_quote: str = ""
    documents: str = ""
    documents_quote: str = ""
    deadline: str = ""
    deadline_quote: str = ""
    funding: str = ""
    funding_quote: str = ""
    how_to_apply: str = ""
    how_to_apply_quote: str = ""
    institution: str = ""
    country: str = ""
    level: str = ""
    is_official: bool = True


EXTRACT_SYSTEM = (
    "You extract scholarship facts from an official university/government page. "
    "CRITICAL RULES: (1) Copy each value from the page text. (2) Each *_quote "
    "field must be a VERBATIM substring of the page (<=200 chars) that supports "
    "the value. (3) If a fact is not on the page, set value and quote to empty "
    "strings. (4) NEVER invent deadlines or amounts. (5) For deadlines, keep "
    "the exact date format from the page."
)

EXTRACT_PROMPT = """Extract scholarship facts from this page text for candidate:
{name}

PAGE URL: {url}
PAGE TEXT (truncated):
{text}

Extract: degree_req, min_grade, english_req, documents, deadline, funding,
how_to_apply, institution, country, level. Every quote MUST be a verbatim
substring of the PAGE TEXT above."""


def _find_quote_in_page(quote: str, page_text: str) -> bool:
    """Tolerant substring check: collapse whitespace, case-insensitive."""
    if not quote:
        return False
    norm = lambda s: re.sub(r"\s+", " ", s).lower().strip()
    return norm(quote) in norm(page_text)


def _make_field(value: str, quote: str, url: str, page_text: str) -> SourcedField:
    """Build a SourcedField; only trust if quote is verbatim in the page."""
    if not value:
        return SourcedField(value="", source_url=url, confidence=Confidence.unverified)
    if quote and _find_quote_in_page(quote, page_text):
        return SourcedField(
            value=value,
            source_url=url,
            source_quote=quote[:300],
            fetched_at=utcnow_iso(),
            confidence=Confidence.high,
        )
    return SourcedField(
        value=value, source_url=url, confidence=Confidence.unverified
    )


def parse_deadline(value: str) -> Optional[date]:
    """Best-effort parse of a deadline string; None if unparseable/past."""
    if not value:
        return None
    text = value.lower()
    months = (
        "january|february|march|april|may|june|july|august|september|"
        "october|november|december|jan|feb|mar|apr|jun|jul|aug|sep|oct|nov|dec"
    )
    m = re.search(rf"(\d{{1,2}})(?:st|nd|rd|th)?\s+({months})\s+(\d{{4}})", text)
    if m:
        day, mon, year = m.group(1), m.group(2), m.group(3)
        try:
            return datetime.strptime(f"{day} {mon[:3]} {year}", "%d %b %Y").date()
        except ValueError:
            try:
                return datetime.strptime(f"{day} {mon} {year}", f"%d {mon} %Y").date()
            except ValueError:
                return None
    m = re.search(rf"({months})\s+(\d{{1,2}})(?:st|nd|rd|th)?,?\s+(\d{{4}})", text)
    if m:
        mon, day, year = m.group(1), m.group(2), m.group(3)
        try:
            return datetime.strptime(f"{day} {mon[:3]} {year}", "%d %b %Y").date()
        except ValueError:
            return None
    m = re.search(r"(\d{4})-(\d{2})-(\d{2})", text)
    if m:
        try:
            return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            return None
    return None


def _assess_eligibility(
    report: EligibilityReport, state: RunState
) -> None:
    """Fill met/unmet/unknown by comparing fields to the profile."""
    profile = state.profile
    met: list[str] = []
    unmet: list[str] = []
    unknown: list[str] = []

    pct = profile.cgpa_pct
    grade = report.min_grade
    if grade.verified and grade.value and pct is not None:
        required = _grade_threshold_pct(grade.value)
        if required is not None:
            if pct >= required:
                met.append(f"CGPA {pct}% meets requirement ({grade.value})")
            else:
                unmet.append(f"CGPA {pct}% below requirement ({grade.value})")
        else:
            unknown.append(f"Could not parse grade threshold: {grade.value}")
    elif not grade.value:
        unknown.append("Minimum grade not stated on page")
    else:
        unknown.append("Minimum grade unverified (no quote match)")

    eng = report.english_req
    ielts = profile.languages_tests.ielts
    if eng.value:
        if ielts:
            met.append(f"English requirement stated: {eng.value}; student has IELTS {ielts}")
        else:
            unmet.append(f"English requirement: {eng.value} (student has no test score)")
    else:
        unknown.append("English requirement not stated")

    if not report.deadline.value:
        unknown.append("Deadline not stated")
    else:
        deadline = parse_deadline(report.deadline.value)
        today = date.today()
        if deadline and deadline < today:
            unmet.append(f"Deadline {deadline.isoformat()} has passed (next cycle expected)")
        elif deadline:
            met.append(f"Deadline {deadline.isoformat()} is upcoming")
        else:
            unknown.append(f"Deadline present but unparseable: {report.deadline.value}")

    if not report.funding.value:
        unknown.append("Funding amount not stated")
    else:
        met.append(f"Funding stated: {report.funding.value[:100]}")

    report.met = met
    report.unmet = unmet
    report.unknown = unknown


def _grade_threshold_pct(text: str) -> Optional[float]:
    """'minimum 60%' -> 60.0; 'CGPA 3.0/4.0' -> 75.0; 'First class' -> 60.0."""
    m = re.search(r"(\d{2}(?:\.\d)?)\s*%", text)
    if m:
        return float(m.group(1))
    m = re.search(r"(\d\.\d)\s*/\s*4", text)
    if m:
        return float(m.group(1)) * 25
    m = re.search(r"(\d\.\d)\s*/\s*10", text)
    if m:
        return float(m.group(1)) * 10
    if re.search(r"first class|1st class", text, re.I):
        return 60.0
    if re.search(r"second class.*upper|2\.1|60%", text, re.I):
        return 60.0
    return None


async def verify_one(
    candidate, state: RunState, llm: LLMClient, inject_fail: str = ""
) -> EligibilityReport:
    """Verify one candidate against its official page (with timeout)."""
    run_id = state.run_id
    url = candidate.official_url or candidate.url
    report = EligibilityReport(candidate_id=candidate.id, official_url=url)

    emit_event(
        run_id, "verifier", "tool_call",
        f"Fetching official page: {url[:80]}",
        {"candidate": candidate.id},
    )
    try:
        page = await asyncio.wait_for(
            fetch_page(url, inject_fail=inject_fail),
            timeout=settings.candidate_timeout,
        )
    except asyncio.TimeoutError:
        emit_event(
            run_id, "verifier", "retry",
            f"Timeout fetching {url[:60]}; marking unverified",
        )
        report.status = "failed"
        report.notes = "fetch timeout"
        return report

    if not page.ok:
        emit_event(
            run_id, "verifier", "fallback",
            f"Fetch failed ({page.note}); marking unverified and continuing",
        )
        report.status = "failed"
        report.notes = page.note
        return report

    emit_event(
        run_id, "verifier", "tool_call",
        f"Extracting facts from {len(page.text)} chars via LLM",
        {"candidate": candidate.id},
    )
    prompt = EXTRACT_PROMPT.format(
        name=candidate.name, url=url, text=page.text[:12000]
    )
    try:
        facts = await asyncio.wait_for(
            llm.structured(prompt, ExtractedFacts, system=EXTRACT_SYSTEM),
            timeout=90.0,
        )
    except (asyncio.TimeoutError, Exception) as exc:
        emit_event(
            run_id, "verifier", "retry",
            f"LLM extraction failed ({type(exc).__name__}); unverified",
        )
        report.status = "failed"
        report.notes = f"extraction failed: {type(exc).__name__}"
        return report

    report.degree_req = _make_field(facts.degree_req, facts.degree_quote, url, page.text)
    report.min_grade = _make_field(facts.min_grade, facts.min_grade_quote, url, page.text)
    report.english_req = _make_field(facts.english_req, facts.english_quote, url, page.text)
    report.documents = _make_field(facts.documents, facts.documents_quote, url, page.text)
    report.deadline = _make_field(facts.deadline, facts.deadline_quote, url, page.text)
    report.funding = _make_field(facts.funding, facts.funding_quote, url, page.text)
    report.how_to_apply = _make_field(
        facts.how_to_apply, facts.how_to_apply_quote, url, page.text
    )
    if facts.institution and not candidate.institution:
        candidate.institution = facts.institution
    if facts.country and not candidate.country:
        candidate.country = facts.country

    _assess_eligibility(report, state)
    verified = sum(
        1 for f in (
            report.degree_req, report.min_grade, report.english_req,
            report.documents, report.deadline, report.funding, report.how_to_apply,
        ) if f.verified
    )
    report.status = "verified" if verified >= 4 else ("partial" if verified >= 1 else "failed")
    emit_event(
        run_id, "verifier", "result",
        f"{candidate.name[:50]}: {report.status} ({verified}/7 fields quoted)",
        {"candidate": candidate.id, "verified": verified},
    )
    return report


async def verify_all(state: RunState, llm: Optional[LLMClient] = None) -> dict:
    """Node: verify candidates in parallel batches (bounded concurrency)."""
    run_id = state.run_id
    client = llm or llm_client
    emit_event(
        run_id, "verifier", "start",
        f"Verifying {len(state.candidates)} candidates in parallel "
        f"(timeout {settings.candidate_timeout}s each)",
    )
    sem = asyncio.Semaphore(4)

    async def bounded(candidate):
        async with sem:
            return await verify_one(candidate, state, client, inject_fail=state.inject_fail)

    tasks = [asyncio.create_task(bounded(c)) for c in state.candidates]
    reports: list[EligibilityReport] = list(await asyncio.gather(*tasks))

    candidate_by_id = {c.id: c for c in state.candidates}
    for r in reports:
        c = candidate_by_id.get(r.candidate_id)
        if c:
            r.candidate_id = r.candidate_id
    verified_count = sum(1 for r in reports if r.status == "verified")
    partial_count = sum(1 for r in reports if r.status == "partial")
    emit_event(
        run_id, "verifier", "done",
        f"Verification complete: {verified_count} verified, {partial_count} partial, "
        f"{len(reports) - verified_count - partial_count} failed",
    )
    return {"reports": reports}
