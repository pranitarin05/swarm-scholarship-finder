"""Opportunity Scout: profile + preferences -> 12-20 real candidates."""
from __future__ import annotations

import asyncio
import re
from typing import Optional

from pydantic import BaseModel

from ..config import settings
from ..events import emit_event
from ..llm import LLMClient, llm_client
from ..state import Candidate, RunState
from ..tools.cache import DiskCache
from ..tools.search import SearchResult, web_search


class QueryPlan(BaseModel):
    """Structured output for the query planner."""

    queries: list[str]

QUERY_SYSTEM = (
    "You are a graduate-scholarship research planner. Produce diverse web "
    "search queries that will surface OFFICIAL scholarship/university pages."
)

QUERY_PROMPT = """Plan 8-10 web search queries for a student with this profile.

Profile: degree={degree}, CGPA%={cgpa}, interests={interests}, skills={skills}
Target countries={countries}, level={level}, budget={budget}

Mix query types: government scholarships, university-funded Master's,
research assistantships/RA positions, tuition-free programs, and named
programs (DAAD, Erasmus Mundus, Commonwealth, etc.). Include the word
"official" or the institution domain when useful.
Return JSON: {{"queries": ["...", ...]}} with exactly 8-10 strings."""

FALLBACK_QUERIES = [
    "DAAD scholarship Master's Germany international students official",
    "Erasmus Mundus Joint Masters scholarship official",
    "fully funded Master's scholarship Canada international students official",
    "University of Toronto graduate funding international Master's official",
    " Waterloo University master's funding international official",
    "NUS graduate scholarship Master's international official",
    "NTU Singapore master's scholarship international official",
    "Germany tuition free master's computer science international students",
    "graduate research assistantship computer science Canada master's official",
    "Commonwealth scholarship Master's official",
]

# Last-resort seed names+URLs (PRD sec.12). Never treated as truth:
# the Verifier must still fetch each official page and quote it live.
SEED_CANDIDATES = [
    ("DAAD Scholarships (Germany)", "https://www.daad.de/en/studying-in-germany/scholarships/daad-scholarships/", "Germany", "scholarship"),
    ("EduCanada Scholarships", "https://www.educanada.ca/scholarships-bourses/non_can/index.aspx?lang=eng", "Canada", "scholarship"),
    ("NUS Graduate Admissions & Scholarships", "https://www.nus.edu.sg/oam/", "Singapore", "scholarship"),
    ("Deutschlandstipendium", "https://www.deutschlandstipendium.de/", "Germany", "scholarship"),
    ("Mitacs Globalink Graduate Fellowship", "https://www.mitacs.ca/en/programs/globalink/globalink-graduate-fellowship", "Canada", "fellowship"),
    ("NTU Singapore Graduate Education", "https://www.ntu.edu.sg/", "Singapore", "program"),
    ("TU Munich Fees & Financial Aid", "https://www.tum.de/en/studies/fees-and-financial-aid", "Germany", "program"),
    ("Singapore International Graduate Award (SINGA)", "https://www.a-star.edu.sg/", "Singapore", "scholarship"),
    ("University of Bonn Master's Programmes", "https://www.uni-bonn.de/en/studying/degree-programs", "Germany", "program"),
    ("Heidelberg University Study Programme", "https://www.uni-heidelberg.de/en/study/subject-catalogue", "Germany", "program"),
    ("McGill Graduate Admissions", "https://www.mcgill.ca/gradapplicants/", "Canada", "program"),
    ("Ontario Tech Graduate Studies", "https://uoit.ca/future-students/graduate/", "Canada", "program"),
]

OFFICIAL_HINTS = (
    ".edu", ".ac.", ".ca", ".de", ".sg", "daad.de", "erasmus", "gov",
    "university", "universität", "college", "institute", "scholarship",
)
AGGREGATOR_HINTS = (
    "scholarship-positions", "mastersportal", "bachelorsportal",
    "studyabroad", "abroadshiksha", "hotcourses", "yoursmobile",
    "scholars4dev", "opportunitiescorners", "gyanodhay", "leverageedu",
    "collegedunia",
)


def is_officialish(url: str) -> bool:
    low = url.lower()
    if any(a in low for a in AGGREGATOR_HINTS):
        return False
    return any(h in low for h in OFFICIAL_HINTS)


def country_of(url: str, title: str = "") -> str:
    low = (url + " " + title).lower()
    mapping = [
        ("daad.de", "Germany"), (".de/", "Germany"), (".de", "Germany"),
        ("canada.ca", "Canada"), (".ca/", "Canada"),
        ("nus.edu.sg", "Singapore"), ("ntu.edu.sg", "Singapore"), (".sg", "Singapore"),
        ("ac.uk", "United Kingdom"), (".uk", "United Kingdom"),
        ("erasmus", "Europe"), ("europa.eu", "Europe"),
        (".edu", "United States"), ("us/", "United States"),
        ("netherlands", "Netherlands"), (".nl", "Netherlands"),
    ]
    for hint, country in mapping:
        if hint in low:
            return country
    return ""


def to_candidates(results: list[SearchResult], query: str) -> list[Candidate]:
    candidates: list[Candidate] = []
    for r in results:
        if not r.url.startswith("http"):
            continue
        official = is_officialish(r.url)
        candidates.append(
            Candidate(
                id=re.sub(r"[^a-z0-9]+", "-", r.url.lower())[-60:].strip("-"),
                name=r.title[:160] or r.url,
                institution="",
                country=country_of(r.url, r.title),
                level="Master's",
                type="scholarship" if "scholarship" in (r.title + r.url).lower() else "program",
                url=r.url,
                official_url=r.url if official else "",
                found_via_query=query,
                discovered_via=r.source,
            )
        )
    return candidates


async def _plan_queries(state: RunState, llm: LLMClient) -> list[str]:
    profile = state.profile
    prompt = QUERY_PROMPT.format(
        degree=profile.degree or "B.Tech CSE",
        cgpa=profile.cgpa_pct or "unknown",
        interests=", ".join(profile.interests[:6]) or "AI/ML",
        skills=", ".join(profile.skills[:6]) or "Python, ML",
        countries=", ".join(state.preferences.countries) or "Canada, Germany, Singapore",
        level=state.preferences.level,
        budget=state.preferences.budget or "fully funded",
    )
    try:
        plan = await llm.structured(prompt, QueryPlan, system=QUERY_SYSTEM)
        queries = [q.strip() for q in plan.queries if q.strip()][:10]
        if len(queries) >= 5:
            return queries
    except Exception:
        pass
    return list(FALLBACK_QUERIES)


async def discover(state: RunState, llm: Optional[LLMClient] = None,
                   inject_fail: str = "") -> dict:
    """Node: plan queries, search the web, dedupe, keep 12-20 candidates."""
    run_id = state.run_id
    emit_event(run_id, "scout", "start", "Planning search queries from profile")
    client = llm or llm_client
    queries = await _plan_queries(state, client)
    emit_event(
        run_id, "scout", "result",
        f"{len(queries)} queries planned: {queries[0][:60]}...",
        {"queries": queries},
    )

    cache = DiskCache()
    fail_mode = inject_fail or state.inject_fail
    all_results: list[SearchResult] = []
    for i, query in enumerate(queries):
        if i:
            await asyncio.sleep(1.5)  # politeness: avoid bot challenges
        emit_event(run_id, "scout", "tool_call", f"[{i+1}/{len(queries)}] Searching: {query[:70]}")
        response = await web_search(query, cache=cache, inject_fail=fail_mode)
        if not response.ok:
            emit_event(
                run_id, "scout", "fallback",
                f"Search failed for '{query[:50]}' ({response.note}); continuing with other queries",
            )
            state.warnings.append(f"search failed: {query[:50]} ({response.note})")
            continue
        emit_event(
            run_id, "scout", "result",
            f"{len(response.results)} results via {response.provider}",
        )
        all_results.extend(to_candidates(response.results, query))

    merged: list[Candidate] = []
    seen_urls: set[str] = set()
    for c in all_results:
        key = c.url.rstrip("/").lower()
        if key in seen_urls:
            continue
        seen_urls.add(key)
        merged.append(c)
    merged.sort(key=lambda c: (0 if c.official_url else 1, c.country or "ZZ"))

    if not merged:
        emit_event(
            run_id, "scout", "fallback",
            "All search providers failed; using built-in seed candidate list "
            "(will still be verified live on official pages)",
        )
        state.warnings.append(
            "search providers unavailable; started from seed candidate list"
        )
        for name, url, country, ctype in SEED_CANDIDATES:
            merged.append(Candidate(
                id=re.sub(r"[^a-z0-9]+", "-", url.lower())[-60:].strip("-"),
                name=name, country=country, level=state.preferences.level,
                type=ctype, url=url, official_url=url,
                found_via_query="seed fallback", discovered_via="seed",
            ))

    kept = merged[:20]
    official_count = sum(1 for c in kept if c.official_url)
    emit_event(
        run_id, "scout", "done",
        f"{len(kept)} candidates collected ({official_count} on official-looking domains)",
        {"candidates": len(kept), "official": official_count},
    )
    return {"candidates": kept, "scout_loops": state.scout_loops + 1}


async def rediscover(state: RunState, note: str) -> dict:
    """Wider queries when the supervisor found too few usable candidates."""
    run_id = state.run_id
    emit_event(
        run_id, "scout", "retry",
        f"Supervisor bounce-back: {note}. Re-running scout with broader queries",
    )
    extra = list(FALLBACK_QUERIES[:6])
    if state.preferences.countries:
        for country in state.preferences.countries:
            extra.append(f"fully funded master's scholarship {country} computer science official page")
    cache = DiskCache()
    fresh: list[Candidate] = []
    for query in extra:
        emit_event(run_id, "scout", "tool_call", f"Widened search: {query[:70]}")
        response = await web_search(query, cache=cache)
        if response.ok:
            fresh.extend(to_candidates(response.results, query))
    existing = {c.url.rstrip("/").lower() for c in state.candidates}
    added = [c for c in fresh if c.url.rstrip("/").lower() not in existing]
    merged = state.candidates + added[:10]
    if len(merged) < 5:
        emit_event(
            run_id, "scout", "fallback",
            "Widened search still thin; supplementing with seed candidate list",
        )
        for name, url, country, ctype in SEED_CANDIDATES:
            if url.rstrip("/").lower() in existing:
                continue
            merged.append(Candidate(
                id=re.sub(r"[^a-z0-9]+", "-", url.lower())[-60:].strip("-"),
                name=name, country=country, level=state.preferences.level,
                type=ctype, url=url, official_url=url,
                found_via_query="seed fallback", discovered_via="seed",
            ))
    emit_event(
        run_id, "scout", "result",
        f"Widened search added {min(len(added), 10)} new candidates "
        f"(total {len(merged)})",
    )
    return {"candidates": merged, "scout_loops": state.scout_loops + 1}
