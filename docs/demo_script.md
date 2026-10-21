# 5-minute demo script — ScholarSwarm

| Time | Show | Say |
|------|------|-----|
| 0:00–0:30 | Rahul's problem on camera: final-year B.Tech, 8.2 CGPA, AI projects, wants Canada/Germany/Singapore, cannot afford full fees. | "Existing tools return 50 links and zero trust. We built seven agents that research, verify every fact on the official page with a source quote, and draft the SOP." |
| 0:30–1:00 | Open the app. Upload `data/sample_rahul/resume.pdf` + `marksheet.pdf`, paste GitHub, choose countries, click **Find my scholarships**. | "One button. No chat window." |
| 1:00–2:30 | Live timeline: Supervisor plans → Analyst parses both PDFs (CGPA normalised) → Enricher hits GitHub API → Scout queries the web → Verifier branches in parallel, one per candidate, each fetching an official page. | "Every card is a real agent with a real tool call. You can see the handoffs." |
| 2:30–3:15 | **Failure moment.** Set the toggle to *Break search API*, start a second run: watch retry → fallback → seed candidates still get verified live. Then show the Fact-Checker event log: a planted deadline with a fake quote gets downgraded to `unverified` and bounced back. | "We break our own system on purpose. It recovers, and it refuses to show unverified numbers as facts." |
| 3:15–4:15 | Results: 10+ opportunities table with official vs discovery-only labels → Top 5 cards with score breakdown (fit/funding/deadline/confidence/risk), verified badges, clickable source quotes, missing items (IELTS). | "The score is deterministic Python, not an LLM opinion. Every fact has a badge." |
| 4:15–4:50 | Open the SOP editor: draft built from Rahul's real projects, scroll the evidence map (project → resume/GitHub URL). Edit a line, download `.md`. | "The SOP may only use claims that trace to the resume or GitHub. Here's the map." |
| 4:50–5:00 | Show `docs/diagram.mmd` + the experiment result: 100% of planted fake deadlines rejected by the critic. | "Architecture and the one experiment we measured." |

## Demo safety nets
- **Demo mode** toggle replays a cached recorded run (79 events) — immune to bad Wi-Fi.
- **Failure toggle** demonstrates recovery on camera without touching code.
- Keyless fallbacks (DDG/Mojeek/seeds + mock LLM) mean the demo runs even if every API key is dead.
