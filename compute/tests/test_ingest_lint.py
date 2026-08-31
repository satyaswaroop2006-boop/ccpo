"""Tests for compute/ingest -- Part I SS I.4's LINT stage, built standalone
(no database access). Two fixture styles, same split every other test file
in this repo uses: small fabricated bundles isolate one behaviour at a
time (docs/DECISIONS.md #5's pattern), and the real `bundle_sbi_
cashback.json` is run through the whole tool end-to-end to lock in the
actual findings from earlier manual review passes -- if this tool ever
disagrees with what was found by hand, that's a real regression, not a
flaky test.
"""
import json
from pathlib import Path

import pytest

from ingest.bundle import citable_entities, declared_sources, load_ingestion_bundle, source_refs
from ingest.cli import main as ingest_main
from ingest.lint import (
    check_engine_compatibility,
    check_provenance_completeness,
    check_source_capture_completeness,
    lint_bundle,
)

INGESTION_DIR = Path(__file__).resolve().parent.parent / "ingestion"


def _minimal_compliant_bundle() -> dict:
    """Every rule-bearing entity cited, every selector engine-supported,
    every source properly captured -- should pass all three lint checks
    cleanly."""
    return {
        "key": "test_card", "name": "Test Card", "network": "visa", "currency": "test_inr",
        "sources": {"src1": {
            "source_type": "mitc", "url": "https://example.com/mitc.pdf",
            "storage_path": "sources/test/src1.pdf", "captured_at": "2026-01-01",
        }},
        "version": {"joining_fee": 0, "annual_fee": 0, "forex_markup": 0.035, "source_refs": ["src1"]},
        "currencies": [
            {"key": "test_inr", "source_refs": ["src1"], "routes": [
                {"key": "stmt", "route_type": "statement_credit", "ratio": 1.0, "source_refs": ["src1"]},
            ]},
        ],
        "earning_rules": [
            {"key": "base", "selector": {"categories": ["grocery"]}, "accrual": {"type": "percentage", "rate": 0.01, "rounding": "floor_paise_per_txn"}, "priority": 10, "source_refs": ["src1"]},
        ],
        "caps": [], "thresholds": [], "exclusions": [], "benefits": [], "surcharges": [],
    }


# ---------------------------------------------------------------------------
# ingest.bundle -- the _source/source_refs and _sources/sources reconciliation
# ---------------------------------------------------------------------------

def test_source_refs_accepts_source_refs_list():
    assert source_refs({"source_refs": ["a", "b"]}) == ("a", "b")


def test_source_refs_accepts_underscore_source_string():
    assert source_refs({"_source": "mitc"}) == ("mitc",)


def test_source_refs_empty_when_neither_present():
    assert source_refs({"_note": "no citation here"}) == ()


def test_source_refs_rejects_non_list_source_refs():
    with pytest.raises(ValueError, match="must be a list"):
        source_refs({"source_refs": "not_a_list"})


def test_declared_sources_accepts_both_spellings():
    assert declared_sources({"sources": {"a": {}}}) == {"a": {}}
    assert declared_sources({"_sources": {"b": {}}}) == {"b": {}}
    assert declared_sources({}) == {}


def test_citable_entities_covers_version_currencies_routes_and_rule_lists():
    bundle = _minimal_compliant_bundle()
    paths = [e.path for e in citable_entities(bundle)]
    assert "version" in paths
    assert any(p.startswith("currencies[0] (test_inr)") for p in paths)
    assert any(p.startswith("currencies[0].routes[0] (stmt)") for p in paths)
    assert any(p.startswith("earning_rules[0] (base)") for p in paths)


# ---------------------------------------------------------------------------
# check_provenance_completeness
# ---------------------------------------------------------------------------

def test_provenance_completeness_passes_on_a_fully_cited_bundle():
    assert check_provenance_completeness(_minimal_compliant_bundle()) == []


def test_provenance_completeness_flags_a_missing_citation():
    bundle = _minimal_compliant_bundle()
    del bundle["earning_rules"][0]["source_refs"]
    issues = check_provenance_completeness(bundle)
    assert len(issues) == 1
    assert "earning_rules[0] (base)" in issues[0].entity
    assert "no source citation" in issues[0].message


def test_provenance_completeness_flags_an_undeclared_source_key():
    bundle = _minimal_compliant_bundle()
    bundle["earning_rules"][0]["source_refs"] = ["not_declared_anywhere"]
    issues = check_provenance_completeness(bundle)
    assert len(issues) == 1
    assert "not_declared_anywhere" in issues[0].message


def test_provenance_completeness_accepts_underscore_convention_too():
    bundle = _minimal_compliant_bundle()
    bundle["earning_rules"][0].pop("source_refs")
    bundle["earning_rules"][0]["_source"] = "src1"
    assert check_provenance_completeness(bundle) == []


# ---------------------------------------------------------------------------
# check_engine_compatibility
# ---------------------------------------------------------------------------

def test_engine_compatibility_passes_on_a_compliant_bundle():
    assert check_engine_compatibility(_minimal_compliant_bundle()) == []


def test_engine_compatibility_flags_every_bad_exclusion_not_just_the_first():
    # mcc_include and txn_max are now engine-supported (Phase 5 Task A) --
    # merchants/date_from are the still-genuinely-unsupported fields used
    # here to exercise this check.
    bundle = _minimal_compliant_bundle()
    bundle["exclusions"] = [
        {"key": "excl_a", "selector": {"merchants": ["bigbasket"]}, "excluded_from": ["rewards"], "source_refs": ["src1"]},
        {"key": "excl_b", "selector": {"date_from": "2026-01-01"}, "excluded_from": ["rewards"], "source_refs": ["src1"]},
    ]
    issues = check_engine_compatibility(bundle)
    entities = {i.entity for i in issues}
    assert "exclusions (excl_a)" in entities
    assert "exclusions (excl_b)" in entities
    assert len(issues) == 2  # both reported, not just the first


def test_engine_compatibility_flags_bundle_from_dict_translation_failure():
    bundle = _minimal_compliant_bundle()
    del bundle["earning_rules"][0]["accrual"]  # bundle_from_dict indexes this directly -> KeyError
    issues = check_engine_compatibility(bundle)
    assert len(issues) == 1
    assert issues[0].entity == "(whole bundle)"


# ---------------------------------------------------------------------------
# check_source_capture_completeness (Part I SS I.1, docs/DECISIONS.md #143)
# ---------------------------------------------------------------------------

def test_source_capture_completeness_passes_when_storage_path_and_captured_at_present():
    assert check_source_capture_completeness(_minimal_compliant_bundle()) == []


def test_source_capture_completeness_flags_a_bare_url_source():
    bundle = _minimal_compliant_bundle()
    bundle["sources"]["src1"] = {"source_type": "mitc", "url": "https://example.com/mitc.pdf"}
    issues = check_source_capture_completeness(bundle)
    assert len(issues) == 1
    assert issues[0].entity == "sources (src1)"
    assert "storage_path" in issues[0].message and "captured_at" in issues[0].message


def test_source_capture_completeness_flags_only_the_missing_field():
    bundle = _minimal_compliant_bundle()
    del bundle["sources"]["src1"]["captured_at"]
    issues = check_source_capture_completeness(bundle)
    assert len(issues) == 1
    assert "missing ['captured_at']" in issues[0].message  # storage_path is present, not also flagged


def test_source_capture_completeness_checks_every_declared_source_independently():
    bundle = _minimal_compliant_bundle()
    bundle["sources"]["src2"] = {"source_type": "faq", "url": "https://example.com/faq"}  # no capture fields
    issues = check_source_capture_completeness(bundle)
    assert len(issues) == 1  # src1 (compliant) untouched; only src2 flagged
    assert issues[0].entity == "sources (src2)"


def test_source_capture_completeness_accepts_the_underscore_sources_spelling_too():
    bundle = _minimal_compliant_bundle()
    bundle["_sources"] = bundle.pop("sources")
    del bundle["_sources"]["src1"]["storage_path"]
    issues = check_source_capture_completeness(bundle)
    assert len(issues) == 1
    assert issues[0].entity == "sources (src1)"


def test_lint_bundle_now_runs_three_checks():
    report = lint_bundle(_minimal_compliant_bundle())
    assert report.checks_run == ("provenance_completeness", "engine_compatibility", "source_capture_completeness")
    assert report.passed is True


# ---------------------------------------------------------------------------
# lint_bundle against the real, previously-hand-reviewed SBI bundle
# ---------------------------------------------------------------------------

def test_lint_bundle_against_real_sbi_bundle_matches_known_findings():
    """Locks in the exact findings docs/DECISIONS.md records by hand.
    Three gaps this bundle used to fail on, all now genuinely closed:
    the two exclusions (mcc_include, txn_max) used to fail engine_
    compatibility until Phase 5 Task A added engine support (#130); the
    currency/route citation gap used to fail provenance_completeness
    until Satya found a real citation (reward_terms Sec 11.1(a) + FAQ
    12/14, #137); and both sources used to fail the source_capture_
    completeness check (#143) until `ingest capture` actually fetched
    and snapshotted them into Supabase Storage, then `--sync-db` pushed
    `storage_path`/`captured_at` onto the live (already-published,
    immutable) `sources` rows (#144/#146) -- confirmed live afterward:
    both objects exist in Storage, both DB rows updated, and CASHBACK's
    `card_versions`/`earning_rules` rows are byte-identical to before
    (sources carries no immutability trigger, verified against 0001_
    init.sql directly, #145). The bundle passes cleanly today. If this
    tool ever finds an error again, that's a real regression worth
    investigating, not noise to silence."""
    bundle = load_ingestion_bundle(INGESTION_DIR / "bundle_sbi_cashback.json")
    report = lint_bundle(bundle)

    assert report.passed is True
    assert report.errors == ()

    assert "C.11" not in " ".join(report.checks_not_implemented)  # sanity: the list itself, not a stray citation
    assert len(report.checks_not_implemented) == 4  # the four C.11 checks this tool deliberately doesn't implement


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def test_cli_lint_exits_1_on_a_failing_bundle(tmp_path, capsys):
    path = tmp_path / "bad_bundle.json"
    bundle = _minimal_compliant_bundle()
    del bundle["earning_rules"][0]["source_refs"]
    path.write_text(json.dumps(bundle))

    exit_code = ingest_main(["lint", str(path)])
    assert exit_code == 1
    out = capsys.readouterr().out
    assert "FAILED" in out
    assert "no source citation" in out


def test_cli_lint_exits_0_on_a_passing_bundle(tmp_path, capsys):
    path = tmp_path / "good_bundle.json"
    path.write_text(json.dumps(_minimal_compliant_bundle()))

    exit_code = ingest_main(["lint", str(path)])
    assert exit_code == 0
    out = capsys.readouterr().out
    assert "PASSED" in out
