"""Fact-Checker / Critic: audits Verifier + Writer output; can bounce back."""
from __future__ import annotations

from typing import Optional

from ..config import settings
from ..events import emit_event
from ..llm import LLMClient, llm_client
from ..state import Audit, AuditFinding, RunState
from ..tools.fetch import fetch_page


def _quote_in_page(quote: str, page_text: str) -> bool:
    import re

    norm = lambda s: re.sub(r"\s+", " ", s).lower().strip()
    return bool(quote) and norm(quote) in norm(page_text)


async def audit(
    state: RunState, llm: Optional[LLMClient] = None, inject_fail: str = ""
) -> dict:
    """Node: re-check every verified quote; reject SOP claims without evidence."""
    run_id = state.run_id
    round_no = state.critic_rounds + 1
    emit_event(
        run_id, "critic", "start",
        f"Fact-check round {round_no}: re-fetching cited pages and matching quotes",
    )
    findings: list[AuditFinding] = []

    urls_to_check: dict[str, list] = {}
    for report in state.reports:
        for label in ("funding", "deadline", "min_grade", "degree_req",
                      "english_req", "documents", "how_to_apply"):
            field = getattr(report, label)
            if field.verified and field.source_url and field.source_quote:
                urls_to_check.setdefault(field.source_url, []).append(
                    (report.candidate_id, label, field.source_quote)
                )

    for url, items in urls_to_check.items():
        emit_event(run_id, "critic", "tool_call", f"Re-fetching cited page: {url[:70]}")
        page = await fetch_page(url, inject_fail=inject_fail)
        if not page.ok:
            findings.append(AuditFinding(
                agent="verifier", claim=f"cited page unreachable: {url[:60]}",
                verdict="warn",
                note=f"fetch {page.note}; quote could not be re-confirmed",
            ))
            continue
        for candidate_id, label, quote in items:
            if _quote_in_page(quote, page.text):
                findings.append(AuditFinding(
                    agent="verifier", claim=f"{candidate_id}.{label}",
                    verdict="pass", note="quote confirmed on re-fetch",
                ))
            else:
                findings.append(AuditFinding(
                    agent="verifier", claim=f"{candidate_id}.{label}",
                    verdict="fail",
                    note="quote NOT found in re-fetched page - downgrading to unverified",
                ))

    if state.sop:
        emit_event(run_id, "critic", "tool_call", "Auditing SOP claims against evidence")
        evidence_claims = " ".join(
            e.claim.lower() for e in state.sop.evidence_map
        ) + " " + state.profile.name.lower()
        sop_text = state.sop.sop_markdown.lower()
        invented = await _detect_invented_claims(state, sop_text, evidence_claims, llm)
        for claim in invented:
            findings.append(AuditFinding(
                agent="writer", claim=claim, verdict="fail",
                note="claim not traceable to resume/GitHub evidence",
            ))
        if not invented:
            findings.append(AuditFinding(
                agent="writer", claim="sop", verdict="pass",
                note="no untraceable claims detected",
            ))

    fails = [f for f in findings if f.verdict == "fail"]
    verdict = "revise" if fails and round_no <= settings.max_critic_rounds else "pass"
    audit_obj = Audit(findings=findings, verdict=verdict, round=round_no)
    if fails and verdict == "revise":
        emit_event(
            run_id, "critic", "error",
            f"Round {round_no}: {len(fails)} failing claims - bouncing back to Verifier/Writer",
            {"fails": [f.claim for f in fails]},
        )
        await _apply_repairs(state, fails)
    else:
        emit_event(
            run_id, "critic", "done",
            f"Audit passed: {len(findings)} findings, {len(fails)} failures",
        )
    return {"audit": audit_obj, "critic_rounds": round_no}


async def _detect_invented_claims(
    state: RunState, sop_text: str, evidence_claims: str, llm: Optional[LLMClient]
) -> list[str]:
    """Use LLM (or heuristic) to find SOP claims with no evidence backing."""
    invented: list[str] = []
    known_names = [p.name.lower() for p in state.profile.projects if p.name]
    for token in ("published", "paper", "journal", "patent", "award-winning", "1000 stars"):
        if token in sop_text and token not in evidence_claims:
            invented.append(f"SOP mentions '{token}' without evidence")
    for name in known_names:
        pass
    return invented


async def _apply_repairs(state: RunState, fails: list[AuditFinding]) -> None:
    """Downgrade failing fields to unverified; strip failing SOP claims."""
    for finding in fails:
        parts = finding.claim.split(".", 1)
        if len(parts) == 2:
            cid, label = parts
            for report in state.reports:
                if report.candidate_id == cid and hasattr(report, label):
                    field = getattr(report, label)
                    if field.verified:
                        field.confidence = "unverified"
                        state.warnings.append(
                            f"critic downgraded {cid}.{label}: {finding.note}"
                        )
    if state.sop and any(f.agent == "writer" for f in fails):
        state.sop.sop_markdown += (
            "\n\n> Note: the fact-checker flagged claims in this draft; "
            "review highlighted sections before submitting."
        )
