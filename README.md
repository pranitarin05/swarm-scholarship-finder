# ScholarSwarm

**7-agent AI scholarship finder.** Upload a resume + marksheet, click one button, and watch seven AI agents research the live internet, verify every fact on official pages (with source quotes), rank the top 5 funded opportunities, and draft a Statement of Purpose — all streamed live to your browser.

> Built for **TetraNoodle AI Hackathon 2026**.

## Why it's different

- **No chatbot.** A visible LangGraph state graph: Supervisor → Analyst → Enricher → Scout → parallel Verifier fan-out → Ranker → Writer → Fact-Checker, with real bounce-back loops.
- **No invented facts.** Every deadline / amount / requirement is a `SourcedField{value, source_url, source_quote, fetched_at, confidence}`. No verbatim quote found on the re-fetched page → labeled `unverified`, never shown as fact, excluded from ranking weight.
- **No black box.** Ranking is deterministic Python (fit 30 / funding 30 / deadline 15 / confidence 15 / risk 10); the UI shows the full breakdown.

## Run it in 3 commands

```bash
git clone <this-repo> && cd scholarswarm
python -m venv .venv && .venv\Scripts\pip install -r requirements.txt   # Windows
cp .env.example .env   # add your GROQ_API_KEY (optional: TAVILY/SERPER/GITHUB_TOKEN)
.venv\Scripts\uvicorn backend.app.main:app --reload
```

Open **http://127.0.0.1:8000**, upload the sample files from `data/sample_rahul/`, click **Find my scholarships**.

Linux/macOS: `python3 -m venv .venv && .venv/bin/pip install -r requirements.txt && .venv/bin/uvicorn backend.app.main:app --reload`

**No API key?** The pipeline still runs: DuckDuckGo/Mojeek search (keyless), a deterministic mock LLM, and a built-in seed candidate list that is still verified live. The demo also works fully offline via **Demo mode** (replays a recorded run from `data/demo_cache/`).

## The agent team

| # | Agent | Job |
|---|-------|-----|
| 0 | **Supervisor** | Plans, routes, loops Scout back if <10 candidates (max 2) |
| 1 | **Profile Analyst** | PyMuPDF + LLM → structured profile, CGPA normalised, missing fields listed |
| 2 | **Profile Enricher** | GitHub REST (repos/stars/languages/READMEs) + links; cross-checks resume claims |
| 3 | **Opportunity Scout** | LLM query planner → live web search (Tavily/Serper → DDG → Mojeek → seeds) → dedupe → 12–20 candidates |
| 4 | **Eligibility Verifier** | N parallel branches: fetch official page, extract quoted Fields, compare to profile (met/unmet/unknown) |
| 5 | **Matcher / Ranker** | Deterministic 5-factor rubric → top 5 + score breakdown; LLM writes "why it fits" from verified facts only |
| 6 | **Application Writer** | SOP + document checklist + evidence map (resume/GitHub only) |
| 7 | **Fact-Checker** | Re-fetches every cited page; quote not found → downgrade to unverified + bounce back (max 2 rounds) |

## API

| Endpoint | What |
|----------|------|
| `POST /api/runs` | multipart: resume, marksheet, preferences JSON, demo_mode, inject_failure |
| `GET /api/runs/{id}/events` | SSE stream of every agent event |
| `GET /api/runs/{id}/result` | Final state: profile, candidates, top 5, SOP, audit |
| `POST /api/runs/{id}/sop` | Regenerate SOP for a chosen candidate |
| `GET /api/runs/{id}/export` | Markdown report download |
| `POST /api/runs/{id}/inject-failure` | Arm `search_down` / `page_404` / `bad_json` |

## Failure handling (shown live)

| Failure | Recovery |
|---------|----------|
| Search API down / bot-challenged | backoff → next provider → seed candidate list (still verified live) |
| Official page 403/404/timeout | retry → mark unverified, continue with other candidates |
| Scanned PDF | OCR if tesseract present, else flag missing fields |
| LLM invalid JSON | 1 repair prompt → next provider → mock |
| GitHub 403/404 | degrade to resume-only enrichment with visible warning |
| <10 usable candidates | Supervisor bounces back to Scout with wider queries (max 2) |
| Hallucinated quote | Fact-Checker re-fetch fails to match → downgrade + revise loop |
| **Demo toggle** | Arm failures from the UI and watch recovery in the timeline |

## Repo layout

```
backend/app/          config, state (Pydantic), events (SSE), llm (provider chain), graph (LangGraph)
backend/app/agents/   supervisor, analyst, enricher, scout, verifier, ranker, writer, critic
backend/app/tools/    search, fetch, github, pdf, cache
backend/scripts/      sample PDF generator, live smoke tests, demo recorder
backend/tests/        34 tests across 4 phase suites
frontend/             plain HTML/CSS/JS + Tailwind CDN (no build step)
data/sample_rahul/    fictional demo resume + marksheet
data/demo_cache/      recorded run for offline demo mode
docs/                 diagram.mmd, experiment_note.md, demo_script.md
decision.txt          every design decision + why
flow.txt              end-to-end flow in plain language
```

## Trust policy

1. Official domains only for facts (`.edu`, `.ac.*`, `daad.de`, government portals). Aggregators only discover names.
2. No quote → `unverified`, never shown as fact.
3. Past deadlines are flagged; fetched_at is always shown.
4. Ranking uses verified facts only; unverified fields reduce confidence and score.
5. SOP claims map 1:1 to resume/GitHub evidence (evidence map included).

## Development

```bash
.venv\Scripts\python -m pytest backend/tests -q     # 34 tests
.venv\Scripts\python backend/scripts/make_sample_pdfs.py  # regenerate samples
.venv\Scripts\python backend/scripts/record_demo.py       # re-record demo cache
```

## License / ethics

Sample persona (Rahul) is fictional. Verify every deadline on the official page before applying — the system tells you the same.
