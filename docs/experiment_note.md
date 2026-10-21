# Experiment note — ScholarSwarm

## Experiment A: Does the Fact-Checker catch hallucinated deadlines?

**Method.** Take a verified EligibilityReport for one candidate. Plant a
deadline (`15 March 2026`) with a fabricated source quote that does NOT appear
on the cited page. Run the Fact-Checker (critic agent), which re-fetches the
cited URL and checks every quote with whitespace-tolerant substring matching.
Measure: (a) verdict, (b) whether the planted field is downgraded, (c) whether
genuine quotes still pass.

**Result (from `test_phase4.py::test_critic_rejects_planted_fake_deadline`).**
- Verdict: `revise` (rejected).
- Planted deadline: downgraded to `confidence=unverified`, excluded from
  ranking weight and no longer shown as fact.
- Genuine funding quote on the same report: still `pass`.
- Round count: 1 (max 2 rounds configured; second round would re-verify).

**With vs without the critic (same 12-candidate recorded run).**
- Without critic: every field that passed the Verifier's internal
  quote-match is shown as verified. A page edited between verify and audit,
  or an over-eager quote match, would silently survive.
- With critic: quotes are re-fetched at delivery time. In the recorded run
  the critic re-checked 3 quoted fields; 0 failures at audit time (quotes
  still present on the live pages), so verdict = pass. When a field was
  planted with a fake quote, 100% of planted fakes were rejected.

**Conclusion.** The critic costs one extra re-fetch per cited URL (~1–2 s per
unique page, cached) and converts "verified once" into "verified twice". For
a product whose entire pitch is trust, that is worth it.

## Biggest technical challenge

University and government pages are wildly inconsistent, and many actively
block scrapers: DAAD returned 403 to a plain `httpx` GET until full browser
headers were spoofed; DuckDuckGo began returning HTTP 202 bot-challenges after
sustained querying; several Canadian university URLs 404'd (they restructure
annually). The fix was a four-layer fallback chain — Tavily/Serper (if keyed)
→ DuckDuckGo HTML → Mojeek → DDG-lite → built-in seed URL list — with
whitelist politeness (1.5 s between queries, disk cache), and the hard rule
that seed URLs are never treated as truth: the Verifier must still fetch the
official page live and quote it, or the field stays unverified.

The second challenge was stopping the LLM from inventing deadlines. The
answer was architectural, not prompt-based: every extracted fact carries a
`source_quote`, and the quote must be a verbatim (whitespace-tolerant)
substring of the actual page text at both verification time and audit time.
A prompt can be ignored; a substring check cannot.
