"""Phase 2 tests: search fallback, fetch, scout query planning and dedupe."""
from __future__ import annotations

import asyncio

import httpx

from backend.app.agents.scout import (
    country_of,
    is_officialish,
    to_candidates,
)
from backend.app.llm import LLMClient, MockProvider, register_mock
from backend.app.state import Preferences, RunState, StudentProfile
from backend.app.tools.fetch import fetch_page
from backend.app.tools.search import SearchResult, web_search


def test_ddg_unwrap_and_official_detection():
    assert is_officialish("https://www.daad.de/en/study-and-research-in-germany/")
    assert is_officialish("https://www.universityofcanada.ca/scholarships/")
    assert not is_officialish("https://www.mastersportal.com/scholarships/123.html")
    wrapped = "//duckduckgo.com/l/?uddg=https%3A%2F%2Fwww.daad.de%2Fxyz"
    from backend.app.tools.search import _unwrap_ddg_url

    assert _unwrap_ddg_url(wrapped) == "https://www.daad.de/xyz"


def test_country_mapping():
    assert country_of("https://www.tum.de/en") == "Germany"
    assert country_of("https://www.nus.edu.sg/programmes") == "Singapore"
    assert country_of("https://www.ubc.ca/") == "Canada"
    assert country_of("https://random.example.com") == ""


def test_to_candidates_prefers_official():
    results = [
        SearchResult(title="DAAD Scholarship", url="https://www.daad.de/scholarship"),
        SearchResult(title="Top 10 Scholarships", url="https://www.mastersportal.com/list"),
    ]
    candidates = to_candidates(results, "daad scholarship")
    assert candidates[0].official_url.startswith("https://www.daad.de")
    assert candidates[1].official_url == ""
    assert candidates[0].country == "Germany"


def test_search_falls_back_to_ddg(monkeypatch):
    """With no Tavily/Serper keys, web_search must use DuckDuckGo."""

    class FakeResp:
        status_code = 200

        @staticmethod
        def raise_for_status():
            pass

        @staticmethod
        def json():
            return {}

        text = """
        <html><body>
        <div class="result"><a class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fwww.daad.de%2Fscholarship">DAAD Scholarship</a>
        <a class="result__snippet">Official DAAD funding for international students</a></div>
        <div class="result"><a class="result__a" href="https://www.university.edu/grad">Grad Funding</a>
        <span class="result__snippet">Graduate admissions</span></div>
        </body></html>
        """

    class FakeClient:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, url):
            return FakeResp()

        async def post(self, url, data=None):
            return FakeResp()

    monkeypatch.setattr(httpx, "AsyncClient", FakeClient)
    response = asyncio.run(web_search("DAAD scholarship Germany"))
    assert response.ok
    assert response.provider == "duckduckgo"
    assert response.results[0].url == "https://www.daad.de/scholarship"


def test_search_injected_failure_returns_empty():
    response = asyncio.run(web_search("anything", inject_fail="search_down"))
    assert not response.ok
    assert "injected" in response.note


def test_fetch_page_injected_404():
    result = asyncio.run(fetch_page("https://example.edu", inject_fail="page_404"))
    assert result.status == 404
    assert not result.ok


def test_fetch_page_parses_html(monkeypatch):
    class FakeResp:
        status_code = 200
        text = "<html><body><article>" + ("Graduate scholarship deadline 15 March. " * 20) + "</article></body></html>"
        request = httpx.Request("GET", "https://example.edu")

        def raise_for_status(self):
            pass

    class FakeClient:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, url):
            return FakeResp()

    monkeypatch.setattr(httpx, "AsyncClient", FakeClient)
    result = asyncio.run(fetch_page("https://example.edu/scholarship"))
    assert result.ok
    assert "scholarship" in result.text.lower()


def test_scout_discovers_candidates_with_mock(monkeypatch):
    from backend.app.agents.scout import discover

    fake_results = [
        SearchResult(
            title=f"Scholarship {i}",
            url=f"https://www.university{i}.edu/scholarship-{i}",
            snippet="Official funding",
            source="duckduckgo",
        )
        for i in range(12)
    ]

    async def fake_search(query, cache=None, inject_fail=""):
        from backend.app.tools.search import SearchResponse

        return SearchResponse(results=fake_results, provider="duckduckgo")

    monkeypatch.setattr("backend.app.agents.scout.web_search", fake_search)
    register_mock(
        "QueryPlan",
        {"queries": ["daad scholarship germany", "erasmundus joint masters"]},
    )
    state = RunState(
        run_id="test-scout",
        preferences=Preferences(countries=["Germany"], level="Master's"),
        profile=StudentProfile(name="Rahul", skills=["Python", "ML"]),
    )
    output = asyncio.run(discover(state, llm=LLMClient(providers=[MockProvider()])))
    assert len(output["candidates"]) >= 10
    assert output["scout_loops"] == 1


def test_scout_survives_total_search_failure(monkeypatch):
    from backend.app.agents.scout import discover
    from backend.app.tools.search import SearchResponse

    async def fake_search(query, cache=None, inject_fail=""):
        return SearchResponse(provider="none", note="injected search failure")

    monkeypatch.setattr("backend.app.agents.scout.web_search", fake_search)
    state = RunState(run_id="test-scout-fail", preferences=Preferences(countries=["Canada"]))
    output = asyncio.run(discover(state, llm=LLMClient(providers=[MockProvider()])))
    # total search failure falls back to the seed candidate list (PRD sec.12),
    # which the verifier must still verify live — never treated as truth
    assert len(output["candidates"]) >= 10
    assert all(c.official_url for c in output["candidates"])
    assert output["scout_loops"] == 1
    assert state.warnings
    assert any("seed" in w for w in state.warnings)
