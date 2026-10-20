"""Phase 3 tests: quote verification, deadline parsing, scoring rubric."""
from __future__ import annotations

from datetime import date

from backend.app.agents.ranker import (
    rank_candidate,
    score_deadline,
    score_fit,
    score_funding,
)
from backend.app.agents.verifier import (
    _assess_eligibility,
    _find_quote_in_page,
    _grade_threshold_pct,
    _make_field,
    parse_deadline,
)
from backend.app.state import (
    Candidate,
    Confidence,
    EligibilityReport,
    Preferences,
    RunState,
    SourcedField,
    StudentProfile,
)

PAGE = "Applications for the 2026 intake close on 15 March 2026. The scholarship covers full tuition and provides a monthly stipend of 934 euros. Applicants need a bachelor's degree with a minimum of 60% marks. English proficiency: IELTS 6.5."


def test_quote_must_be_verbatim_substring():
    assert _find_quote_in_page("close on 15 March 2026", PAGE)
    assert _find_quote_in_page("CLOSE ON 15   MARCH 2026", PAGE)
    assert not _find_quote_in_page("deadline is 20 February 2026", PAGE)


def test_make_field_unverified_when_quote_missing_or_wrong():
    field = _make_field("15 March 2026", "", "https://x.edu", PAGE)
    assert field.confidence == Confidence.unverified
    assert not field.verified
    fabricated = _make_field("15 March 2026", "totally different text", "https://x.edu", PAGE)
    assert fabricated.confidence == Confidence.unverified
    real = _make_field("15 March 2026", "close on 15 March 2026", "https://x.edu", PAGE)
    assert real.verified
    assert real.confidence == Confidence.high


def test_deadline_parsing_formats():
    assert parse_deadline("15 March 2026") == date(2026, 3, 15)
    assert parse_deadline("March 15, 2026") == date(2026, 3, 15)
    assert parse_deadline("2026-03-15") == date(2026, 3, 15)
    assert parse_deadline("rolling admissions") is None
    assert parse_deadline("") is None


def test_grade_threshold_parsing():
    assert _grade_threshold_pct("minimum 60% marks") == 60.0
    assert _grade_threshold_pct("CGPA 3.0/4.0") == 75.0
    assert _grade_threshold_pct("CGPA 7.0/10") == 70.0
    assert _grade_threshold_pct("First class degree") == 60.0
    assert _grade_threshold_pct("no number here") is None


def test_funding_scoring_prefers_fully_funded():
    def report_with(text):
        r = EligibilityReport()
        r.funding = SourcedField(
            value=text, source_url="https://x.edu", source_quote="x",
            confidence=Confidence.high,
        )
        return r

    full = score_funding(report_with("Fully funded: tuition + 934 EUR/month stipend"))
    partial = score_funding(report_with("Partial funding of 5000 EUR"))
    none = score_funding(report_with(""))
    assert full >= 9.5
    assert full > partial > none


def test_deadline_scoring_and_past_deadline_flagged():
    future = date.today().replace(year=date.today().year + 1)
    r = EligibilityReport()
    r.deadline = SourcedField(
        value=future.strftime("%d %B %Y"), source_url="u", source_quote="q",
        confidence=Confidence.high,
    )
    assert score_deadline(r, today=date.today()) >= 7.0
    past = EligibilityReport()
    past.deadline = SourcedField(
        value="1 January 2020", source_url="u", source_quote="q",
        confidence=Confidence.high,
    )
    assert score_deadline(past, today=date.today()) == 0.0


def test_rank_candidate_uses_verified_only():
    profile = StudentProfile(name="Rahul", cgpa_pct=85.0, skills=["Python", "ML"],
                             target_countries=["Germany"])
    candidate = Candidate(id="daad", name="DAAD Master's", level="Master's",
                          country="Germany", official_url="https://www.daad.de/x")
    report = EligibilityReport(candidate_id="daad", official_url="https://www.daad.de/x",
                               status="verified")
    report.funding = SourcedField(value="Fully funded: tuition + stipend",
                                  source_url="u", source_quote="q",
                                  confidence=Confidence.high)
    report.min_grade = SourcedField(value="minimum 60%", source_url="u",
                                    source_quote="q", confidence=Confidence.high)
    report.deadline = SourcedField(
        value=date.today().replace(year=date.today().year + 1).strftime("%d %B %Y"),
        source_url="u", source_quote="q", confidence=Confidence.high)
    report.degree_req = SourcedField(value="bachelor's degree", source_url="u",
                                     source_quote="q", confidence=Confidence.high)
    ranked = rank_candidate(candidate, report, profile)
    assert ranked.total_score > 50
    assert set(ranked.breakdown) == {"fit", "funding", "deadline", "confidence", "risk"}

    unverified_report = EligibilityReport(candidate_id="daad", status="failed")
    ranked2 = rank_candidate(candidate, unverified_report, profile)
    assert ranked2.total_score < ranked.total_score


def test_assess_flags_passed_deadline_and_missing_ielts():
    profile = StudentProfile(name="Rahul", cgpa_pct=85.0, languages_tests={})
    profile.languages_tests.ielts = ""
    state = RunState(run_id="t", profile=profile, preferences=Preferences())
    report = EligibilityReport(candidate_id="x")
    report.min_grade = SourcedField(value="minimum 70%", source_url="u",
                                    source_quote="q", confidence=Confidence.high)
    report.english_req = SourcedField(value="IELTS 6.5", source_url="u",
                                      source_quote="q", confidence=Confidence.high)
    report.deadline = SourcedField(value="1 January 2020", source_url="u",
                                   source_quote="q", confidence=Confidence.high)
    report.funding = SourcedField(value="", source_url="", confidence=Confidence.unverified)
    _assess_eligibility(report, state)
    assert any("passed" in u.lower() or "passed" in u for u in report.unmet)
    assert any("IELTS" in u for u in report.unmet)
    assert any("CGPA" in m for m in report.met)
    assert any("Funding" in u or "funding" in u for u in report.unknown)


def test_fit_scores_meeting_cgpa():
    profile = StudentProfile(cgpa_pct=85.0, interests=["AI"])
    candidate = Candidate(level="Master's")
    good = EligibilityReport()
    good.min_grade = SourcedField(value="minimum 60%", source_url="u",
                                  source_quote="q", confidence=Confidence.high)
    low = EligibilityReport()
    low.min_grade = SourcedField(value="minimum 90%", source_url="u",
                                 source_quote="q", confidence=Confidence.high)
    assert score_fit(profile, good, candidate) > score_fit(profile, low, candidate)
