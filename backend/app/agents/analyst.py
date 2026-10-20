"""Profile Analyst: resume + marksheet PDFs -> structured StudentProfile."""
from __future__ import annotations

import re

from ..config import settings
from ..events import emit_event
from ..llm import LLMClient, llm_client
from ..state import StudentProfile
from ..tools.pdf import extract_pdf_text

SYSTEM = (
    "You are a meticulous admissions-profile analyst. Extract ONLY what is "
    "present in the text. Never invent values. Use empty string / empty list "
    "for anything absent, and list absent-but-important items in "
    "missing_fields (e.g. 'IELTS score', 'CGPA scale')."
)

PROMPT = """Extract the student profile from this resume/marksheet text.

TEXT:
{text}

Preferences known from the form (keep these exact):
- target_countries: {countries}
- target_level: {level}
- budget: {budget}

Normalise the CGPA: set cgpa_raw (as printed), cgpa_scale ("10", "4" or
"100"), and copy the preferences into the profile. Also produce 3-8 concrete
interests (e.g. "Machine Learning", "Graduate Research in Canada")."""


def _prefill(text: str, state) -> StudentProfile:
    """Deterministic extraction used when no LLM key is available."""
    profile = StudentProfile(
        target_countries=list(state.preferences.countries),
        target_level=state.preferences.level,
        budget=state.preferences.budget,
    )
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        low = line.lower()
        if not profile.name and re.match(r"^[a-z]+\s+[a-z]+$", low):
            profile.name = line.title()
        if "@" in line and not profile.email:
            match = re.search(r"[\w.+-]+@[\w-]+\.[\w.]+", line)
            if match:
                profile.email = match.group(0)
        if not profile.degree and any(
            k in low for k in ("b.tech", "bachelor", "b.e.")
        ):
            profile.degree = line
        cgpa = re.search(r"(cgpa|gpa)\s*[:=]?\s*([0-9.]+)", low)
        if cgpa and not profile.cgpa_raw:
            profile.cgpa_raw = cgpa.group(2)
            profile.cgpa_scale = "10"
    return profile


async def analyse(state, llm: LLMClient | None = None) -> dict:
    """Node: parse PDFs into StudentProfile with missing-field tracking."""
    run_id = state.run_id
    emit_event(run_id, "analyst", "start", "Parsing resume and marksheet PDFs")
    texts: list[str] = []
    for label, path in (("resume", state.resume_path),
                        ("marksheet", state.marksheet_path)):
        if not path:
            continue
        emit_event(run_id, "analyst", "tool_call", f"Extracting text from {label} PDF")
        try:
            result = extract_pdf_text(path)
        except Exception as exc:
            emit_event(
                run_id, "analyst", "error",
                f"Could not read {label} PDF: {type(exc).__name__}",
            )
            state.errors.append(f"{label} PDF unreadable")
            continue
        if not result.ok:
            emit_event(
                run_id, "analyst", "retry",
                f"{label} PDF looks scanned ({result.note or 'low text'}); "
                "falling back to whatever text exists",
            )
        else:
            emit_event(
                run_id, "analyst", "result",
                f"{label}: {result.pages} pages, {len(result.text)} chars",
            )
        texts.append(f"--- {label.upper()} ---\n{result.text}")
    if not texts:
        emit_event(run_id, "analyst", "error", "No readable document text")
        return {"errors": state.errors + ["no readable documents"]}

    combined = "\n\n".join(texts)
    client = llm or llm_client
    prompt = PROMPT.format(
        text=combined[:12000],
        countries=", ".join(state.preferences.countries) or "not specified",
        level=state.preferences.level,
        budget=state.preferences.budget or "not specified",
    )
    emit_event(run_id, "analyst", "tool_call", "Extracting structured profile via LLM")
    try:
        profile = await client.structured(prompt, StudentProfile, system=SYSTEM)
    except Exception as exc:
        emit_event(
            run_id, "analyst", "fallback",
            f"LLM structured extraction failed ({type(exc).__name__}); "
            "using deterministic parser",
        )
        profile = _prefill(combined, state)

    profile.target_countries = profile.target_countries or list(
        state.preferences.countries
    )
    profile.target_level = profile.target_level or state.preferences.level
    profile.budget = profile.budget or state.preferences.budget
    profile.normalize_cgpa()
    if profile.cgpa_pct is None and "cgpa" not in " ".join(profile.missing_fields).lower():
        profile.missing_fields.append("CGPA scale (assumed 10.0 scale)")
    if not profile.languages_tests.ielts and "IELTS" not in profile.missing_fields:
        profile.missing_fields.append("IELTS/English test score")
    emit_event(
        run_id, "analyst", "done",
        f"Profile ready: {profile.name or 'unknown'} | "
        f"{profile.degree or 'degree?'} | CGPA% {profile.cgpa_pct or 'n/a'} | "
        f"missing: {', '.join(profile.missing_fields) or 'none'}",
        {"missing_fields": profile.missing_fields},
    )
    return {"profile": profile}
