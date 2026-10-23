"""GitHub REST API client with disk cache and graceful degradation."""
from __future__ import annotations

from typing import Optional

import httpx

from ..config import settings
from ..state import GithubInfo, GithubRepo
from .cache import DiskCache

API = "https://api.github.com"


class GithubClient:
    """Read-only GitHub lookups. Never raises on 403/404: degrades instead."""

    def __init__(
        self,
        token: Optional[str] = None,
        cache: Optional[DiskCache] = None,
        inject_fail: str = "",
    ) -> None:
        self.token = token if token is not None else settings.github_token
        self.cache = cache or DiskCache()
        self.inject_fail = inject_fail

    def _headers(self) -> dict[str, str]:
        headers = {
            "Accept": "application/vnd.github+json",
            "User-Agent": settings.user_agent,
        }
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        return headers

    async def _get(self, url: str, params: Optional[dict] = None):
        if self.inject_fail == "github_404":
            raise httpx.HTTPStatusError(
                "injected failure", request=None, response=httpx.Response(404)  # type: ignore[arg-type]
            )
        cached = self.cache.get(f"gh:{url}:{params or {}}")
        if cached is not None:
            return cached
        async with httpx.AsyncClient(timeout=settings.fetch_timeout) as client:
            resp = await client.get(
                url, params=params, headers=self._headers(), follow_redirects=True
            )
        if resp.status_code in (403, 404, 429):
            raise httpx.HTTPStatusError(
                f"github {resp.status_code}", request=resp.request, response=resp
            )
        resp.raise_for_status()
        data = resp.json()
        self.cache.set(f"gh:{url}:{params or {}}", data)
        return data

    async def enrich(self, username: str) -> GithubInfo:
        """Profile + repos + languages + stars + activity, or a warned stub."""
        info = GithubInfo(username=username, profile_url=f"https://github.com/{username}")

        if not self.token:
            info.warnings.append(
                "No GITHUB_TOKEN set — using unauthenticated GitHub API "
                "(rate-limited to 60 req/hr). Set GITHUB_TOKEN in .env for "
                "higher limits and private repo access."
            )

        try:
            profile = await self._get(f"{API}/users/{username}")
        except httpx.HTTPError as exc:
            info.warnings.append(
                f"GitHub profile unavailable for '{username}' "
                f"({type(exc).__name__}); enrichment degraded to resume-only"
            )
            return info
        info.public_repos = profile.get("public_repos", 0)
        info.profile_url = profile.get("html_url", info.profile_url)

        try:
            raw_repos = await self._get(
                f"{API}/users/{username}/repos",
                params={"per_page": 100, "sort": "updated"},
            )
        except httpx.HTTPError as exc:
            info.warnings.append(f"GitHub repos unavailable ({type(exc).__name__})")
            return info

        for raw in raw_repos:
            info.repos.append(
                GithubRepo(
                    name=raw.get("name", ""),
                    description=raw.get("description") or "",
                    language=raw.get("language") or "",
                    stars=raw.get("stargazers_count", 0),
                    fork=bool(raw.get("fork")),
                    url=raw.get("html_url", ""),
                    updated_at=raw.get("updated_at") or "",
                    topics=raw.get("topics") or [],
                )
            )

        info.total_stars = sum(r.stars for r in info.repos)

        # Count languages; prefer original repos (non-forks) for accuracy
        counts: dict[str, int] = {}
        original_repos = [r for r in info.repos if not r.fork] or info.repos
        for repo in original_repos:
            if repo.language:
                counts[repo.language] = counts.get(repo.language, 0) + 1
        info.top_languages = [
            lang for lang, _ in sorted(counts.items(), key=lambda kv: -kv[1])[:6]
        ]
        info.activity = _activity_label(info.repos)
        return info


    async def readme(self, username: str, repo: str) -> str:
        """Fetch a repo README as plain text (empty string if unavailable)."""
        try:
            async with httpx.AsyncClient(timeout=settings.fetch_timeout) as client:
                resp = await client.get(
                    f"{API}/repos/{username}/{repo}/readme",
                    headers={**self._headers(), "Accept": "application/vnd.github.raw"},
                    follow_redirects=True,
                )
            if resp.status_code != 200:
                return ""
            return resp.text[:4000]
        except httpx.HTTPError:
            return ""


def _activity_label(repos: list[GithubRepo]) -> str:
    recent = [r for r in repos if r.updated_at and r.updated_at[:4] >= "2025"]
    n = len(repos)
    if n == 0:
        return "no public repositories"
    if len(recent) >= 5:
        return "very active (5+ repos updated in the last year)"
    if recent:
        return "active (recent repository updates)"
    return "low recent activity"


def username_from_url(url: str) -> str:
    """Extract a GitHub username from any form of GitHub URL.

    Handles:
    - https://github.com/username
    - https://github.com/username/repo
    - https://github.com/username?tab=repositories#heading
    - @username
    - plain 'username'
    - www.github.com/username
    """
    url = (url or "").strip()

    # Strip leading '@' (common copy-paste artefact)
    if url.startswith("@"):
        url = url[1:]

    # No URL scheme at all — treat as bare username or 'github.com/...'
    if "github.com" not in url.lower():
        # Already a bare username (no slashes, no dots)
        return url.split("/")[0].split("?")[0].split("#")[0].strip()

    # Normalise: drop scheme and www
    import re as _re
    url = _re.sub(r"^https?://(www\.)?", "", url, flags=_re.IGNORECASE)

    # Now url is like: github.com/username[/repo][?query][#fragment]
    path = url.split("github.com/", 1)[1]  # 'username[/...][?...][#...]'
    username = path.split("/")[0]           # drop any sub-path
    username = username.split("?")[0]       # drop query string
    username = username.split("#")[0]       # drop fragment
    return username.strip()
