"""Profile Enricher: GitHub + supplied links -> evidence-backed profile."""
from __future__ import annotations

from typing import Optional

import httpx

from ..config import settings
from ..events import emit_event
from ..state import EnrichedProfile, Evidence, StudentProfile
from ..tools.cache import DiskCache
from ..tools.github import GithubClient, username_from_url

MAX_LINKS = 4


async def _fetch_link_text(url: str, cache: DiskCache) -> str:
    cached = cache.get(f"link:{url}")
    if cached is not None:
        return cached.get("text", "")
    try:
        async with httpx.AsyncClient(
            timeout=settings.fetch_timeout,
            headers={"User-Agent": settings.user_agent},
            follow_redirects=True,
        ) as client:
            resp = await client.get(url)
        if resp.status_code != 200:
            return ""
        from bs4 import BeautifulSoup

        soup = BeautifulSoup(resp.text, "html.parser")
        for tag in soup(["script", "style", "nav", "footer"]):
            tag.decompose()
        text = " ".join(soup.get_text(" ").split())[:4000]
        cache.set(f"link:{url}", {"text": text})
        return text
    except httpx.HTTPError:
        return ""


def build_evidence(profile: StudentProfile, info) -> tuple[
    list[Evidence], list[str], list[str], list[str]
]:
    """Compare resume claims to GitHub reality; return evidence/claims/strengths/gaps."""
    evidence: list[Evidence] = []
    unverified: list[str] = []
    strengths: list[str] = []
    gaps: list[str] = []

    if info.username:
        base = info.profile_url or f"https://github.com/{info.username}"
        evidence.append(
            Evidence(
                claim=f"GitHub account with {info.public_repos} public repos, "
                      f"{info.total_stars} stars total",
                source_url=base,
                snippet=f"top languages: {', '.join(info.top_languages) or 'n/a'}",
            )
        )
        if info.top_languages:
            strengths.append(
                f"GitHub shows {len(info.repos)} public repositories with "
                f"primary languages {', '.join(info.top_languages[:3])}"
            )
        if info.total_stars >= 10:
            strengths.append(f"Community interest: {info.total_stars} GitHub stars")
        ml_repos = [
            r for r in info.repos
            if any(k in (r.name + r.description).lower()
                   for k in ("ml", "ai", "deep", "nlp", "torch", "tensorflow",
                             "llm", "rag", "model"))
        ]
        if ml_repos:
            starred = [r for r in ml_repos if r.stars >= 5]
            strengths.append(
                f"{len(ml_repos)} AI/ML-related repositories"
                + (f", {len(starred)} with 5+ stars" if starred else "")
            )
    else:
        gaps.append("No GitHub URL supplied: profile not independently verified")

    for project in profile.projects:
        if not project.name:
            continue
        match = next(
            (r for r in info.repos
             if _similar(project.name, r.name)
             or _similar(project.name, r.description)),
            None,
        )
        if match:
            evidence.append(
                Evidence(
                    claim=f"Resume project '{project.name}' has code evidence",
                    source_url=match.url,
                    snippet=(match.description or match.name)[:200],
                )
            )
        else:
            unverified.append(
                f"Resume project '{project.name}' has no matching public GitHub repo"
            )
    if not profile.projects:
        unverified.append("Resume lists no projects to cross-check")
    return evidence, unverified, strengths, gaps


def _similar(a: str, b: str) -> bool:
    ta = {t for t in "".join(c if c.isalnum() else " " for c in a.lower()).split() if len(t) > 2}
    tb = {t for t in "".join(c if c.isalnum() else " " for c in (b or "").lower()).split() if len(t) > 2}
    if not ta or not tb:
        return False
    return len(ta & tb) / len(ta) >= 0.6


async def enrich(state, github_client: Optional[GithubClient] = None) -> dict:
    """Node: GitHub REST + extra links + resume cross-check."""
    run_id = state.run_id
    emit_event(run_id, "enricher", "start", "Enriching profile from GitHub and links")
    client = github_client or GithubClient(cache=DiskCache())
    username = username_from_url(state.github_url)

    if username:
        emit_event(
            run_id, "enricher", "tool_call",
            f"GitHub API: fetching user '{username}' + repositories",
        )
        info = await client.enrich(username)
        for warning in info.warnings:
            emit_event(run_id, "enricher", "fallback", warning)
            state.warnings.append(warning)
        if not info.warnings:
            emit_event(
                run_id, "enricher", "result",
                f"GitHub: {len(info.repos)} repos, {info.total_stars} stars, "
                f"languages {', '.join(info.top_languages[:3]) or 'n/a'}",
            )
    else:
        emit_event(
            run_id, "enricher", "result",
            "No GitHub URL supplied; skipping GitHub enrichment",
        )
        from ..state import GithubInfo

        info = GithubInfo(username="")

    links = [u for u in state.extra_links if u][:MAX_LINKS]
    cache = DiskCache()
    fetched_links: list[str] = []
    for url in links:
        emit_event(run_id, "enricher", "tool_call", f"Fetching extra link: {url}")
        text = await _fetch_link_text(url, cache)
        if text:
            fetched_links.append(url)
            emit_event(run_id, "enricher", "result", f"Extracted {len(text)} chars from {url}")
        else:
            emit_event(
                run_id, "enricher", "retry",
                f"Could not read {url} (blocked or empty); continuing without it",
            )

    evidence, unverified, strengths, gaps = build_evidence(state.profile, info)
    if info.warnings:
        gaps.extend(info.warnings)
    emit_event(
        run_id, "enricher", "handoff",
        f"EnrichedProfile ready: {len(evidence)} evidence links, "
        f"{len(strengths)} strengths, {len(unverified)} unverified claims",
        {"evidence": len(evidence), "strengths": len(strengths)},
    )
    enriched = EnrichedProfile(
        github=info,
        extra_links=fetched_links,
        evidence=evidence,
        strengths=strengths,
        gaps=gaps,
        unverified_resume_claims=unverified,
    )
    emit_event(run_id, "enricher", "done", "Profile enrichment complete")
    return {"enriched": enriched}
