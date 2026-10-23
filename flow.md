# ScholarSwarm — FLOW

How the system works, step by step, in plain language.
Companion to [`decision.md`](decision.md); requirements in `docs/PRD.md`.

---

## 1. Happy path (one click -> top 5 scholarships + SOP)

```
[Browser]  student uploads resume.pdf + marksheet.pdf,
           fills form (name, CGPA, interests, countries,
           budget, GitHub URL, extra links),
           clicks "Find my scholarships"
      |
      v
POST /api/runs  --->  run_id created, files saved to
      |                data/runs/<id>/, run starts in background
      v
SUPERVISOR  plan (run plan event, preferences read)
      |
      v
PROFILE ANALYST  PDFs -> StudentProfile
      |           (name, degree, CGPA normalised to %/4.0,
      |            skills, projects, missing fields = "unknown")
      v
PROFILE ENRICHER  GitHub API + extra links -> EnrichedProfile
      |           (repos, stars, languages, activity, evidence[],
      |            strengths, gaps, unverified resume claims)
      v
OPPORTUNITY SCOUT  LLM builds 8-12 search queries
      |   ^          -> live web search -> 12-20 candidates
      |   |
      |   +--- fewer than 10 usable? --+-- re-run with wider
      |              |                 |   queries (max 2 loops)
      |              +-----------------+
      v
SUPERVISOR check_candidates  (>= 10 usable?)
      |
      |  parallel, bounded to 4 at a time (Semaphore(4)),
      |  one task per candidate, per-candidate timeout
      +--------+--------+--------+
      v        v        v        v
  VERIFIER  VERIFIER  VERIFIER  VERIFIER   each fetches the
      |        |        |        |         OFFICIAL page and
      |        |        |        |         extracts Fields with
      +--------+--------+--------+         verbatim source quotes
      v
MATCHER / RANKER  deterministic rubric ->
      |            top 5 + score breakdown
      v
APPLICATION WRITER  SOP + document checklist + evidence map
      |             (for opportunity #1, resume/GitHub facts only)
      v
FACT-CHECKER  re-fetch every cited URL, substring-match every quote
      |
      +-- pass ---------------------------------> FINAL REPORT
      |
      +-- fail (max 2 rounds) --> back to VERIFIER (field-level
                                  downgrade to unverified, notes
                                  attached) or re-run WRITER
      v
UI shows: opportunities table (10+), top-5 cards with score
breakdown, sources + verified/unverified badges, missing items,
SOP editor with download, Markdown export
```

The graph that implements this is compiled in
`backend/app/graph.py::build_graph()`:
`START -> supervisor -> analyst -> enricher -> scout -> check_candidates`
with conditional edges `check_candidates -> rediscover | verifier`
(`rediscover -> check_candidates` loops back to Scout),
then `verifier -> ranker -> writer -> critic`, and conditional
`critic -> verifier (revise) | END (done)` gated by
`critic_rounds < max_critic_rounds` (2).

---

## 2. State flow (who writes what, in order)

One shared typed state (`RunState`, Pydantic v2, in `backend/app/state.py`)
is passed node to node. Each agent only writes its own section:

| Agent | Writes |
|---|---|
| supervisor | `run_plan`, route decision |
| analyst | `profile: StudentProfile` |
| enricher | `enriched: EnrichedProfile` |
| scout | `candidates: list[Candidate]` (12–20), `scout_loops` |
| verifier x N | `reports: list[EligibilityReport]` (every requirement/funding/deadline a `Field`) |
| ranker | `ranked: list[Ranked]` (top 5 + breakdown) |
| writer | `sop: SOPDraft` (markdown + evidence map) |
| critic | `audit: Audit` (findings, verdict, round), may set route back to `verifier` |

Rules:
- Agents hand off **typed objects**, never free text.
- Nothing is deleted; later agents only add or update.
- If a field is unknown, it stays `unknown` — never guessed.
- `emit_event()` is called on every transition, so state changes and timeline
  messages can never disagree.

---

## 3. Event / SSE flow (what the UI timeline shows)

```
every agent step
      |
      v
emit Event {ts, agent, type, message, data}
types: start | tool_call | result | retry | fallback | handoff | error | done
      |
      v
per-run asyncio queue + JSONL log (data/runs/<id>/events.jsonl)
      |
      v
GET /api/runs/<id>/events   (SSE stream; replays history first,
      |                       then live tail)
      v
browser EventSource
      |
      v
8 agent cards update live:
idle -> working -> (retrying) -> done / failed
+ scrolling log lines + handoff arrows
```

Why a queue **and** a JSONL file: the file is what demo mode replays and what
judges can inspect after the fact; the queue is what makes it live.

---

## 4. Failure flows (each one is recoverable)

| Failure | Detect | Recover |
|---|---|---|
| Search API down / quota / bot-challenge | HTTP error or empty results | retry with backoff -> next provider (Tavily -> Serper -> DDG -> Mojeek -> DDG-lite) -> cached seed queries |
| Official page 403/404/timeout/blocked | status code or empty text | retry with browser headers -> mark fields `unverified`, continue with other candidates |
| Scanned PDF (no text) | text length below threshold | OCR if tesseract installed -> else flag missing fields in UI |
| LLM returns invalid JSON | Pydantic validation error | 1 repair prompt -> next provider -> deterministic mock |
| LLM rate limit (429) | HTTP 429 | backoff -> next provider |
| GitHub 403/404 / rate limit | response code | use token if set -> else resume-only enrichment with visible warning |
| <10 usable candidates | `check_candidates` node counts | loop `rediscover -> scout` with broader queries (max 2) |
| Hallucinated deadline / amount | critic can't find `source_quote` in the re-fetched page | reject -> bounce back to verifier with notes (max 2) -> else downgrade field to `unverified` -> unverified fields lose ranking weight |
| Candidate too slow | per-candidate timeout | cancel that branch, continue the others |
| Injected failure (demo toggle) | armed on purpose | `POST /api/runs/{id}/inject-failure` -> next search/fetch/LLM call fails on purpose -> normal recovery chain shows retry/fallback/done in the timeline |

---

## 5. Demo mode flow (offline safety net)

```
toggle "Demo mode" ON before clicking Run
      |
      v
server loads data/demo_cache/events.json + result.json
      |
      v
replays events into the SSE stream with realistic timing
      |
      v
results page renders the cached report,
SOP editor works on the cached draft
-> demo survives bad Wi-Fi / dead quotas
```

---

## 6. API surface (what exists and why)

| Endpoint | Why |
|---|---|
| `POST /api/runs` | multipart: resume, marksheet, preferences, `demo_mode`, `inject_failure` -> creates run, starts graph in background -> returns `run_id` |
| `GET /api/runs/{id}/events` | SSE stream of Event objects for the timeline |
| `GET /api/runs/{id}/result` | final state: profile, candidates (10+), top 5, reports, sop, audit, breakdowns |
| `GET /api/runs/{id}/status` | cheap poll for run completion |
| `POST /api/runs/{id}/sop` | regenerate the SOP for the opportunity the student actually chose |
| `GET /api/runs/{id}/export` | Markdown report download |
| `POST /api/runs/{id}/inject-failure` | arm `search_down` / `page_404` / `bad_json` for the next call |
| `GET /api/health` | deploy health check |
| `GET /api/sample/{files,profile}` | one-click fill of the Rahul demo persona |

---

## 7. Trust flow (how a fact earns its "verified" badge)

```
Scout finds name via aggregator
      |
      v
Verifier fetches OFFICIAL page (Field: value + source_url + source_quote)
      |
      v
substring check: quote must appear verbatim in page text
      |
      +-- no quote --> confidence=unverified, excluded from ranking weight
      |
      +-- quote ok  --> confidence high/medium, enters Ranker
      v
Critic RE-fetches the same URL at delivery time and repeats the check
      |
      +-- mismatch --> downgrade + graph bounces back (max 2 rounds)
      +-- match ----> badge shown in UI, click opens the source
```
