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
from ingest.lint import check_engine_compatibility, check_provenance_completeness, lint_bundle

INGESTION_DIR = Path(__file__).resolve().parent.parent / "ingestion"


def _minimal_compliant_bundle() -> dict:
    """Every rule-bearing entity cited, every selector engine-supported --
    should pass both lint checks cleanly."""
    return {
        "key": "test_card", "name": "Test Card", "network": "visa", "currency": "test_inr",
        "sources": {"src1": {"source_type": "mitc", "url": "https://example.com/mitc.pdf"}},
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
# lint_bundle against the real, previously-hand-reviewed SBI bundle
# ---------------------------------------------------------------------------

def test_lint_bundle_against_real_sbi_bundle_matches_known_findings():
    """Locks in the exact findings docs/DECISIONS.md records by hand. The
    bundle's two exclusions (mcc_include, txn_max) USED to fail engine_
    compatibility -- as of Phase 5 Task A (docs/DECISIONS.md #130) both
    selector fields are engine-supported, so lint now accepts them. The
    currency/route citation gap (#129) USED to fail provenance_
    completeness -- Satya resolved it by finding a real citation
    (reward_terms Sec 11.1(a) + FAQ 12/14, docs/DECISIONS.md #137) rather
    than amending Part I to exempt it, so both entities now cite a source
    too. The bundle passes cleanly today -- an intended reject->accept
    flip both times, not a regression. If this tool ever finds something
    different again, that's a real change worth investigating, not noise
    to silence."""
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
