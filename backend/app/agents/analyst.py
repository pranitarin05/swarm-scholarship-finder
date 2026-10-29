"""Profile Analyst: resume + marksheet PDFs -> structured StudentProfile."""
from __future__ import annotations

import re

from ..events import emit_event
from ..llm import LLMClient, llm_client
from ..state import StudentProfile
from ..tools.pdf import extract_pdf_text

# Maximum characters of combined PDF text sent to the LLM (16k chars ~ 4k tokens, safe for Groq limits).
MAX_TEXT_CHARS = 16_000

SYSTEM = (
    "You are a meticulous admissions-profile analyst. Extract ONLY what is "
    "present in the text. Never invent values. Use empty string / empty list "
    "for anything absent, and list absent-but-important items in "
    "missing_fields (e.g. 'IELTS score', 'CGPA scale').\n\n"
    "For cgpa_scale: use '10' if the raw CGPA looks like 1–10, '4' if it "
    "looks like 0–4, '100' if it already is a percentage. Set cgpa_scale "
    "even if you have to infer it from the numeric range."
)

PROMPT = """Extract the student profile from this resume/marksheet text.

TEXT:
{text}

Preferences known from the form (keep these exact):
- target_countries: {countries}
- target_level: {level}
- budget: {budget}

Instructions:
1. Extract name, email, degree, institution, graduation_year.
2. Find every skill mentioned (programming languages, frameworks, tools).
3. List all projects with name, summary, tech stack.
4. List work experience (role, organisation, period, details).
5. List publications if any.
6. Extract IELTS, TOEFL, GRE scores from the marksheet.
7. Normalise the CGPA:
   - cgpa_raw: the number exactly as printed (e.g. '8.7', '3.5', '87')
   - cgpa_scale: '10', '4', or '100' — infer from the range if not stated
8. Produce 4–8 concrete research interests (e.g. 'Machine Learning',
   'Computer Vision', 'Graduate Research in Canada').
9. Put the form preferences into target_countries / target_level / budget."""


# ---------------------------------------------------------------------------
# Deterministic fallback (no LLM)
# ---------------------------------------------------------------------------

def _infer_cgpa_scale(raw_str: str) -> str:
    """Infer the most likely CGPA scale from the raw value."""
    try:
        v = float(raw_str)
        if v > 10:
            return "100"
        if v > 4:
            return "10"
        return "4"
    except (ValueError, TypeError):
        return ""


def _prefill(text: str, state) -> StudentProfile:
    """Deterministic extraction used when no LLM key is available."""
    profile = StudentProfile(
        target_countries=list(state.preferences.countries),
        target_level=state.preferences.level,
        budget=state.preferences.budget,
    )
    skills_found: list[str] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        low = line.lower()

        # Name: two-to-four capitalised words at line start, no digits
        if not profile.name and re.match(r"^[A-Z][a-z]+(?:\s+[A-Z][a-z]+){1,3}$", line):
            profile.name = line

        # Email
        if "@" in line and not profile.email:
            m = re.search(r"[\w.+-]+@[\w-]+\.[\w.]+", line)
            if m:
                profile.email = m.group(0)

        # Degree
        if not profile.degree and any(
            k in low for k in (
                "b.tech", "bachelor", "b.e.", "m.tech", "master",
                "m.sc", "msc", "b.sc", "bsc", "phd", "doctor"
            )
        ):
            profile.degree = line[:120]

        # Institution
        if not profile.institution and any(
            k in low for k in (
                "university", "institute", "college", "iit", "nit",
                "bits", "iiit", "vit", "amity"
            )
        ):
            profile.institution = line[:120]

        # CGPA / GPA
        cgpa = re.search(
            r"(?:cgpa|gpa|grade\s*point|score)\s*[:/]?\s*([0-9]+(?:\.[0-9]+)?)",
            low,
        )
        if cgpa and not profile.cgpa_raw:
            profile.cgpa_raw = cgpa.group(1)
            profile.cgpa_scale = _infer_cgpa_scale(profile.cgpa_raw)

        # IELTS / TOEFL / GRE
        ielts = re.search(r"ielts\s*[:/]?\s*([0-9]+(?:\.[0-9]+)?)", low)
        if ielts and not profile.languages_tests.ielts:
            profile.languages_tests.ielts = ielts.group(1)
        toefl = re.search(r"toefl\s*[:/]?\s*([0-9]+)", low)
        if toefl and not profile.languages_tests.toefl:
            profile.languages_tests.toefl = toefl.group(1)
        gre = re.search(r"gre\s*[:/]?\s*([0-9]+)", low)
        if gre and not profile.languages_tests.gre:
            profile.languages_tests.gre = gre.group(1)

        # Skills (comma/bullet lists of known tech keywords)
        _TECH = (
            "python", "java", "javascript", "typescript", "c++", "c#", "go",
            "rust", "sql", "r ", "matlab", "react", "angular", "vue", "node",
            "django", "flask", "fastapi", "spring", "tensorflow", "pytorch",
            "keras", "scikit", "pandas", "numpy", "docker", "kubernetes",
            "aws", "azure", "gcp", "git", "linux", "html", "css", "mongodb",
            "mysql", "postgresql", "redis", "spark", "hadoop", "kafka",
            "langgraph", "langchain", "openai", "huggingface", "opencv",
        )
        for tech in _TECH:
            if tech in low and tech.strip().title() not in skills_found:
                skills_found.append(tech.strip().title())

    profile.skills = skills_found[:20]
    return profile


# ---------------------------------------------------------------------------
# Main analyst node
# ---------------------------------------------------------------------------

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
                f"{label}: {result.pages} pages, {len(result.text)} chars extracted",
            )
        texts.append(f"--- {label.upper()} ---\n{result.text}")

    if not texts:
        emit_event(run_id, "analyst", "error", "No readable document text")
        return {"errors": state.errors + ["no readable documents"]}

    combined = "\n\n".join(texts)
    client = llm or llm_client
    prompt = PROMPT.format(
        text=combined[:MAX_TEXT_CHARS],
        countries=", ".join(state.preferences.countries) or "not specified",
        level=state.preferences.level,
        budget=state.preferences.budget or "not specified",
    )
    emit_event(
        run_id, "analyst", "tool_call",
        f"Sending {min(len(combined), MAX_TEXT_CHARS):,} chars to LLM for structured extraction",
    )
    try:
        profile = await client.structured(prompt, StudentProfile, system=SYSTEM)
    except Exception as exc:
        emit_event(
            run_id, "analyst", "fallback",
            f"LLM structured extraction failed ({type(exc).__name__}); "
            "using deterministic parser",
        )
        profile = _prefill(combined, state)

    # Override with form preferences if LLM forgot them
    profile.target_countries = profile.target_countries or list(
        state.preferences.countries
    )
    profile.target_level = profile.target_level or state.preferences.level
    profile.budget = profile.budget or state.preferences.budget

    # Guard: infer cgpa_scale if LLM returned a raw value but no scale
    if profile.cgpa_raw and not profile.cgpa_scale:
        profile.cgpa_scale = _infer_cgpa_scale(profile.cgpa_raw)
        emit_event(
            run_id, "analyst", "retry",
            f"cgpa_scale missing from LLM; inferred '{profile.cgpa_scale}' "
            f"from raw value '{profile.cgpa_raw}'",
        )

    profile.normalize_cgpa()

    # Annotate genuinely missing fields
    mf_lower = " ".join(profile.missing_fields).lower()
    if profile.cgpa_pct is None and "cgpa" not in mf_lower:
        profile.missing_fields.append("CGPA scale (could not be determined)")
    if not profile.languages_tests.ielts and "ielts" not in mf_lower:
        profile.missing_fields.append("IELTS/English test score")

    emit_event(
        run_id, "analyst", "done",
        f"Profile ready: {profile.name or 'unknown'} | "
        f"{profile.degree or 'degree?'} | CGPA% {profile.cgpa_pct or 'n/a'} | "
        f"skills: {len(profile.skills)} | projects: {len(profile.projects)} | "
        f"missing: {', '.join(profile.missing_fields) or 'none'}",
        {"missing_fields": profile.missing_fields},
    )
    return {"profile": profile}
