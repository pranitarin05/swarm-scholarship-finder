"""Web page fetching: httpx + trafilatura/BS4, retry, disk cache."""
from __future__ import annotations

import asyncio
from typing import Optional

import httpx

from ..config import settings
from .cache import DiskCache

MAX_RETRIES = 2


class FetchResult:
    def __init__(self, url: str, text: str, status: int, note: str = "") -> None:
        self.url = url
        self.text = text
        self.status = status
        self.note = note

    @property
    def ok(self) -> bool:
        return self.status == 200 and len(self.text.strip()) >= 200


def extract_text(html: str) -> str:
    """trafilatura first (cleaner), BS4 as fallback."""
    try:
        import trafilatura

        extracted = trafilatura.extract(html, include_comments=False)
        if extracted and len(extracted.strip()) >= 200:
            return extracted
    except Exception:
        pass
    try:
        from bs4 import BeautifulSoup

        soup = BeautifulSoup(html, "html.parser")
        for tag in soup(["script", "style", "nav", "footer", "header", "noscript"]):
            tag.decompose()
        return " ".join(soup.get_text(" ").split())
    except Exception:
        return ""


def _browser_headers() -> dict[str, str]:
    return {
        "User-Agent": settings.user_agent,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,"
                  "image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "gzip, deflate, br",
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1",
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "none",
        "Sec-Fetch-User": "?1",
        "Cache-Control": "max-age=0",
    }


async def fetch_page(
    url: str,
    cache: Optional[DiskCache] = None,
    timeout: Optional[float] = None,
    inject_fail: str = "",
) -> FetchResult:
    """Fetch one page with cache + retry. Never raises."""
    cache = cache or DiskCache()
    if inject_fail == "page_404":
        return FetchResult(url, "", 404, "injected 404 failure")
    if inject_fail == "page_timeout":
        return FetchResult(url, "", 0, "injected timeout")

    cached = cache.get(f"fetch:{url}")
    if cached is not None:
        return FetchResult(url, cached.get("text", ""), 200, "cache")

    last_note = ""
    for attempt in range(MAX_RETRIES + 1):
        try:
            async with httpx.AsyncClient(
                timeout=timeout or settings.fetch_timeout,
                headers=_browser_headers(),
                follow_redirects=True,
            ) as client:
                resp = await client.get(url)
            if resp.status_code in (403, 405) and attempt == 0:
                # One retry with a curl-like UA: some WAFs block browser UAs oddly
                async with httpx.AsyncClient(
                    timeout=timeout or settings.fetch_timeout,
                    headers={"User-Agent": "curl/8.5.0", "Accept": "*/*"},
                    follow_redirects=True,
                ) as client:
                    resp = await client.get(url)
            if resp.status_code in (429, 503) and attempt < MAX_RETRIES:
                await asyncio.sleep(1.5 * (attempt + 1))
                last_note = f"HTTP {resp.status_code}, retrying"
                continue
            if resp.status_code != 200:
                return FetchResult(url, "", resp.status_code, f"HTTP {resp.status_code}")
            text = extract_text(resp.text)
            cache.set(f"fetch:{url}", {"text": text})
            return FetchResult(url, text, 200)
        except httpx.TimeoutException:
            last_note = "timeout"
            if attempt < MAX_RETRIES:
                await asyncio.sleep(1.0)
                continue
        except httpx.HTTPError as exc:
            last_note = type(exc).__name__
            if attempt < MAX_RETRIES:
                await asyncio.sleep(1.0)
                continue
        except Exception as exc:
            last_note = type(exc).__name__
            break
    return FetchResult(url, "", 0, last_note or "fetch failed")
