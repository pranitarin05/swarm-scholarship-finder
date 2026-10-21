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
    """Keyless fallback: scrape the DDG HTML endpoint (with 202 backoff)."""
    import asyncio

    cached = cache.get(f"search:ddg:{query}")
    if cached is not None:
        return SearchResponse(
            results=[SearchResult(**r) for r in cached], provider="duckduckgo", note="cache"
        )
    url = "https://html.duckduckgo.com/html/"
    headers = {
        "User-Agent": settings.user_agent,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Referer": "https://duckduckgo.com/",
    }
    results: list[SearchResult] = []
    last_note = ""
    for attempt in range(2):
        if attempt:
            await asyncio.sleep(2.0)
        try:
            async with httpx.AsyncClient(
                timeout=20.0, headers=headers, follow_redirects=True
            ) as client:
                resp = await client.post(url, data={"q": query, "kl": "us-en"})
            if resp.status_code == 202:
                last_note = "HTTP 202 (bot challenge), backing off"
                continue
            if resp.status_code != 200:
                last_note = f"HTTP {resp.status_code}"
                continue
            from bs4 import BeautifulSoup

            soup = BeautifulSoup(resp.text, "html.parser")
            for block in soup.select("div.result")[:10]:
                link = block.select_one("a.result__a")
                snippet_el = block.select_one("a.result__snippet, div.result__snippet")
                if not link:
                    continue
                results.append(
                    SearchResult(
                        title=link.get_text(" ", strip=True),
                        url=_unwrap_ddg_url(link.get("href", "")),
                        snippet=snippet_el.get_text(" ", strip=True)[:300]
                        if snippet_el else "",
                        source="duckduckgo",
                    )
                )
            if results:
                break
            last_note = "no results parsed"
        except (httpx.HTTPError, ValueError) as exc:
            last_note = type(exc).__name__
    if not results:
        return SearchResponse(provider="duckduckgo", note=last_note or "empty")
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


async def _mojeek(query: str, cache: DiskCache) -> SearchResponse:
    """Keyless fallback #2: Mojeek (bot-friendly, simple HTML)."""
    import asyncio

    cached = cache.get(f"search:mojeek:{query}")
    if cached is not None:
        return SearchResponse(
            results=[SearchResult(**r) for r in cached], provider="mojeek", note="cache"
        )
    url = f"https://www.mojeek.com/search?q={quote_plus(query)}"
    try:
        async with httpx.AsyncClient(
            timeout=30.0, headers={"User-Agent": settings.user_agent}, follow_redirects=True
        ) as client:
            resp = await client.get(url)
    except httpx.HTTPError as exc:
        return SearchResponse(provider="mojeek", note=type(exc).__name__)
    if resp.status_code == 429:
        await asyncio.sleep(3.0)
        try:
            async with httpx.AsyncClient(
                timeout=30.0, headers={"User-Agent": settings.user_agent}, follow_redirects=True
            ) as client:
                resp = await client.get(url)
        except httpx.HTTPError as exc:
            return SearchResponse(provider="mojeek", note=type(exc).__name__)
    if resp.status_code != 200:
        return SearchResponse(provider="mojeek", note=f"HTTP {resp.status_code}")
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(resp.text, "html.parser")
    results: list[SearchResult] = []
    for ul in soup.select("ul.results-standard, ul#results")[:1]:
        for li in ul.select("li")[:10]:
            a = li.select_one("a.title, h2 a, a.ob")
            p = li.select_one("p.s, .s")
            if not a:
                continue
            results.append(
                SearchResult(
                    title=a.get_text(" ", strip=True),
                    url=a.get("href", ""),
                    snippet=p.get_text(" ", strip=True)[:300] if p else "",
                    source="mojeek",
                )
            )
    if not results:
        return SearchResponse(provider="mojeek", note="no results parsed")
    cache.set(f"search:mojeek:{query}", [r.__dict__ for r in results])
    return SearchResponse(results=results, provider="mojeek")


async def _lite_duckduckgo(query: str, cache: DiskCache) -> SearchResponse:
    """DDG lite endpoint (simpler HTML, sometimes survives rate limits)."""
    cached = cache.get(f"search:ddglite:{query}")
    if cached is not None:
        return SearchResponse(
            results=[SearchResult(**r) for r in cached], provider="ddg-lite", note="cache"
        )
    url = "https://lite.duckduckgo.com/lite/"
    try:
        async with httpx.AsyncClient(
            timeout=30.0, headers={"User-Agent": settings.user_agent}, follow_redirects=True
        ) as client:
            resp = await client.post(url, data={"q": query})
    except httpx.HTTPError as exc:
        return SearchResponse(provider="ddg-lite", note=type(exc).__name__)
    if resp.status_code != 200:
        return SearchResponse(provider="ddg-lite", note=f"HTTP {resp.status_code}")
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(resp.text, "html.parser")
    results: list[SearchResult] = []
    rows = soup.select("table tr")
    i = 0
    while i < len(rows):
        link = rows[i].select_one("a")
        if link and link.get("href", "").startswith("http"):
            snippet = rows[i + 1].get_text(" ", strip=True)[:300] if i + 1 < len(rows) else ""
            results.append(
                SearchResult(
                    title=link.get_text(" ", strip=True),
                    url=_unwrap_ddg_url(link.get("href", "")),
                    snippet=snippet,
                    source="ddg-lite",
                )
            )
            i += 2
        else:
            i += 1
        if len(results) >= 10:
            break
    if not results:
        return SearchResponse(provider="ddg-lite", note="no results parsed")
    cache.set(f"search:ddglite:{query}", [r.__dict__ for r in results])
    return SearchResponse(results=results, provider="ddg-lite")


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
    chain.extend([_mojeek, _duckduckgo, _lite_duckduckgo])

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
