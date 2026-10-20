"""Web search: Tavily/Serper if keyed, DuckDuckGo HTML always available."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional
from urllib.parse import parse_qs, quote_plus, unquote, urlparse

import httpx

from ..config import settings
from .cache import DiskCache


@dataclass
class SearchResult:
    title: str = ""
    url: str = ""
    snippet: str = ""
    source: str = ""


@dataclass
class SearchResponse:
    results: list[SearchResult] = field(default_factory=list)
    provider: str = ""
    note: str = ""

    @property
    def ok(self) -> bool:
        return len(self.results) > 0


async def _tavily(query: str, cache: DiskCache) -> SearchResponse:
    cached = cache.get(f"search:tavily:{query}")
    if cached is not None:
        return SearchResponse(
            results=[SearchResult(**r) for r in cached], provider="tavily", note="cache"
        )
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(
            "https://api.tavily.com/search",
            json={
                "api_key": settings.tavily_api_key,
                "query": query,
                "max_results": 8,
                "include_answer": False,
            },
        )
        resp.raise_for_status()
        data = resp.json()
    results = [
        SearchResult(
            title=r.get("title", ""),
            url=r.get("url", ""),
            snippet=r.get("content", "")[:300],
            source="tavily",
        )
        for r in data.get("results", [])
    ]
    cache.set(f"search:tavily:{query}", [r.__dict__ for r in results])
    return SearchResponse(results=results, provider="tavily")


async def _serper(query: str, cache: DiskCache) -> SearchResponse:
    cached = cache.get(f"search:serper:{query}")
    if cached is not None:
        return SearchResponse(
            results=[SearchResult(**r) for r in cached], provider="serper", note="cache"
        )
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(
            "https://google.serper.dev/search",
            headers={"X-API-KEY": settings.serper_api_key},
            json={"q": query, "num": 8},
        )
        resp.raise_for_status()
        data = resp.json()
    results = [
        SearchResult(
            title=r.get("title", ""),
            url=r.get("link", ""),
            snippet=r.get("snippet", "")[:300],
            source="serper",
        )
        for r in data.get("organic", [])
    ]
    cache.set(f"search:serper:{query}", [r.__dict__ for r in results])
    return SearchResponse(results=results, provider="serper")


async def _duckduckgo(query: str, cache: DiskCache) -> SearchResponse:
    """Keyless fallback: scrape the DDG HTML endpoint."""
    cached = cache.get(f"search:ddg:{query}")
    if cached is not None:
        return SearchResponse(
            results=[SearchResult(**r) for r in cached], provider="duckduckgo", note="cache"
        )
    url = f"https://html.duckduckgo.com/html/?q={quote_plus(query)}"
    async with httpx.AsyncClient(
        timeout=30.0, headers={"User-Agent": settings.user_agent}, follow_redirects=True
    ) as client:
        resp = await client.get(url)
    if resp.status_code != 200:
        return SearchResponse(provider="duckduckgo", note=f"HTTP {resp.status_code}")
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(resp.text, "html.parser")
    results: list[SearchResult] = []
    for block in soup.select("div.result")[:10]:
        link = block.select_one("a.result__a")
        snippet_el = block.select_one("a.result__snippet, div.result__snippet")
        if not link:
            continue
        href = link.get("href", "")
        real = _unwrap_ddg_url(href)
        results.append(
            SearchResult(
                title=link.get_text(" ", strip=True),
                url=real,
                snippet=snippet_el.get_text(" ", strip=True)[:300] if snippet_el else "",
                source="duckduckgo",
            )
        )
    cache.set(f"search:ddg:{query}", [r.__dict__ for r in results])
    return SearchResponse(results=results, provider="duckduckgo")


def _unwrap_ddg_url(href: str) -> str:
    """DDG wraps links: //duckduckgo.com/l/?uddg=<encoded>."""
    if "uddg=" in href:
        parsed = urlparse("https:" + href if href.startswith("//") else href)
        target = parse_qs(parsed.query).get("uddg", [""])[0]
        return unquote(target)
    if href.startswith("//"):
        return "https:" + href
    return href


async def web_search(
    query: str,
    cache: Optional[DiskCache] = None,
    inject_fail: str = "",
) -> SearchResponse:
    """Provider chain: Tavily -> Serper -> DuckDuckGo. Never raises."""
    cache = cache or DiskCache()
    if inject_fail == "search_down":
        return SearchResponse(provider="none", note="injected search failure")

    chain = []
    if settings.tavily_api_key:
        chain.append(_tavily)
    if settings.serper_api_key:
        chain.append(_serper)
    chain.append(_duckduckgo)

    notes: list[str] = []
    for provider_fn in chain:
        try:
            response = await provider_fn(query, cache)
            if response.ok:
                return response
            notes.append(f"{response.provider}: {response.note or 'empty'}")
        except (httpx.HTTPError, KeyError, ValueError) as exc:
            notes.append(f"{provider_fn.__name__}: {type(exc).__name__}")
    return SearchResponse(provider="none", note="; ".join(notes) or "all providers failed")
