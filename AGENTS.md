# ScholarSwarm - Agent Rules (persist across the session)

Hackathon project for TetraNoodle AI Hackathon 2026. Multi-agent AI Scholarship
Finder. Build docs: decision.txt and flow.txt at repo root (read them first).

## Goal
7-agent LangGraph system: resume + marksheet + GitHub in -> 10+ real opportunities
-> verified top 5 with source-quoted facts -> SOP draft -> fact-checked report.
NOT a chatbot.

## Judging marks
30 autonomy (visible agent collaboration, loops, critic bounce-back) | 25 real
research (live search, official pages, GitHub API) | 20 trust (every fact is a
SourcedField with quote + URL, else "unverified") | 10 website | 15 failure
handling (fallback chains, retry, failure injection, demo mode).

## Hard rules
1. Every extracted fact = SourcedField{value, source_url, source_quote, fetched_at, confidence}.
   No quote -> confidence=unverified, never shown as fact. Never invent deadlines/amounts.
2. Official domains only for facts. Aggregators only discover names.
3. Ranking is deterministic Python (fit 30 / funding 30 / deadline 15 / confidence 15 / risk 10).
   LLM writes explanations from verified facts only.
4. SOP uses only resume/GitHub evidence; include evidence map.
5. Secrets in .env only. Never commit .env. Never log keys.
6. Bounded loops: scout widen <= 2, critic rounds <= 2.
7. Every agent emits Event objects (start|tool_call|result|retry|fallback|handoff|error|done)
   via events.emit_event(run_id, ...) so the SSE timeline shows real work.
8. LLM access only through backend/app/llm.py (provider chain + JSON repair retry).
9. Frontend: plain HTML/CSS/vanilla JS + Tailwind CDN. No npm build step.
10. After each phase: run tests, fix, commit, stop and wait for "continue".

## Stack
Python 3.14 venv (.venv), FastAPI + SSE, LangGraph, Pydantic v2, httpx,
PyMuPDF, trafilatura, BeautifulSoup4, reportlab (sample PDFs), pytest.
LLM: Groq primary, Gemini/OpenAI optional fallback, deterministic mock last.
Search: Tavily/Serper if key, else DuckDuckGo HTML fallback.

## Cut order if time runs out
(1) extra enrichment links, (2) Playwright, (3) Docker, (4) UI polish.
NEVER cut: Verifier, Fact-Checker, failure handling, source quotes.
