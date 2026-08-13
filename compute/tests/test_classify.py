"""Golden-style hand-computed scenarios for optimiser/classify.py (Phase 4
frontier/classify slice). Universe = {syn_ecom, syn_flat, syn_miles}, same
spend as test_candidates.py (ecommerce Rs6,00,000/yr + utilities
Rs30,000/yr) so the single-card pv_exact numbers (syn_ecom Rs15,900.00,
syn_flat Rs9,450.00, syn_miles Rs1,350.00) are reused, not re-derived.

DOWNGRADE and HOLD have no real fixture yet (see classify.py's own
docstring -- no card has a `family_key`, no user-constraint model exists),
so those two are tested against directly-constructed `SubsetResult`
entries (same "bypass the upstream stage, test this module's own logic"
pattern Stage 3's tests use for SpendSegment, docs/DECISIONS.md #5) rather
than left untested.
"""
from decimal import Decimal

from app.repository import SyntheticCatalogRepository
from engine.evaluate import EvaluateAssumptions
from engine.normalise import CategorySpend, SpendInput
from optimiser.classify import ADD, CLOSE, DOWNGRADE, HOLD, KEEP, NOT_MATERIAL, OPTIONAL, classify_portfolio
from optimiser.enumerate import SubsetResult, enumerate_subsets

REPO = SyntheticCatalogRepository()
CURRENCIES = REPO.get_currencies()
ASSUMPTIONS = EvaluateAssumptions(primary_routes={"synth_points": "stmt"})

SPEND = SpendInput(category_spend=(
    CategorySpend(category="ecommerce", channel="online", annual_amount=Decimal("600000")),
    CategorySpend(category="utilities", annual_amount=Decimal("30000")),
))


def _universe():
    return [REPO.get_card_bundle("syn_ecom"), REPO.get_card_bundle("syn_flat"), REPO.get_card_bundle("syn_miles")]


def _enumerate(max_cards=2):
    return enumerate_subsets(_universe(), CURRENCIES, SPEND, ASSUMPTIONS, cardinality_mode="up_to", max_cards=max_cards)


def test_keep_and_optional_from_real_two_card_portfolio():
    # P = {syn_ecom, syn_flat}: Rs20,000/mo @5% (Rs1,000) on syn_ecom +
    # Rs30,000/mo overflow ecommerce @1.5% on syn_flat (Rs450) + all
    # Rs2,500/mo utilities @1.5% on syn_flat (Rs37.50, since 1.5% beats
    # syn_ecom's 1% base) = (1,000+450+37.50)*12 = Rs17,850.00/yr. syn_ecom's
    # own Rs2,40,000 allocated (waiver-eligible) spend clears its Rs1,00,000
    # threshold outright -- fee waived on this subset too.
    results = _enumerate(max_cards=2)
    by_key = {r.subset_key: r for r in results}
    assert by_key["syn_ecom+syn_flat"].pv_exact == Decimal("17850.00")

    result = classify_portfolio(
        results, _universe(), CURRENCIES, SPEND, portfolio_card_keys=["syn_ecom", "syn_flat"], assumptions=ASSUMPTIONS,
    )
    assert result.pv_exact == Decimal("17850.00")
    by_card = {c.card_key: c for c in result.owned}

    # ICV(syn_ecom|P) = 17,850.00 - pv({syn_flat})[9,450.00] = 8,400.00
    assert by_card["syn_ecom"].icv == Decimal("8400.00")
    assert by_card["syn_ecom"].label == KEEP
    # ICV(syn_flat|P) = 17,850.00 - pv({syn_ecom})[15,900.00] = 1,950.00
    assert by_card["syn_flat"].icv == Decimal("1950.00")
    assert by_card["syn_flat"].label == KEEP  # default icv_meaningful=1,000 < 1,950

    # Overlap(c|P) = standalone(c) - ICV(c|P); both come out equal here
    # since pv(P) enters symmetrically for a 2-card portfolio.
    assert by_card["syn_ecom"].overlap == Decimal("7500.00")
    assert by_card["syn_flat"].overlap == Decimal("7500.00")


def test_higher_icv_meaningful_reclassifies_the_marginal_card_as_optional():
    results = _enumerate(max_cards=2)
    result = classify_portfolio(
        results, _universe(), CURRENCIES, SPEND, portfolio_card_keys=["syn_ecom", "syn_flat"],
        assumptions=ASSUMPTIONS, icv_meaningful=Decimal("5000"),
    )
    by_card = {c.card_key: c for c in result.owned}
    assert by_card["syn_ecom"].label == KEEP  # 8,400.00 still clears 5,000
    assert by_card["syn_flat"].label == OPTIONAL  # 1,950.00 <= 5,000


def test_add_candidate_via_lookup_and_via_not_material():
    # syn_miles as an ADD candidate on top of P={syn_ecom, syn_flat}. The
    # 3-card pv_exact isn't hand-derivable cheaply through the real
    # milestone/voucher pipeline by hand here, so it's supplied as a
    # pre-enumerated (fabricated) SubsetResult -- this test is about
    # classify.py's lookup-and-subtract arithmetic, not a second proof of
    # the engine's milestone maths (already covered by test_candidates.py/
    # test_goldens.py).
    base_results = _enumerate(max_cards=2)
    three_card = SubsetResult(
        subset_key="syn_ecom+syn_flat+syn_miles", card_keys=("syn_ecom", "syn_flat", "syn_miles"), size=3,
        pv_planned=Decimal("29850.00"), pv_exact=Decimal("29850.00"), repair_applied=False, gap=Decimal("0"),
        allocation=base_results[0].allocation, card_results={},
    )
    results = base_results + (three_card,)

    result = classify_portfolio(
        results, _universe(), CURRENCIES, SPEND, portfolio_card_keys=["syn_ecom", "syn_flat"],
        assumptions=ASSUMPTIONS, candidate_card_keys=["syn_miles"],
    )
    assert len(result.candidates) == 1
    candidate = result.candidates[0]
    # ICV(syn_miles+|P) = 29,850.00 - 17,850.00 = 12,000.00
    assert candidate.icv == Decimal("12000.00")
    assert candidate.label == ADD
    assert candidate.overlap is None

    result_small = classify_portfolio(
        results, _universe(), CURRENCIES, SPEND, portfolio_card_keys=["syn_ecom", "syn_flat"],
        assumptions=ASSUMPTIONS, candidate_card_keys=["syn_miles"], icv_meaningful=Decimal("50000"),
    )
    assert result_small.candidates[0].label == NOT_MATERIAL


def test_owned_candidate_is_never_double_reported():
    results = _enumerate(max_cards=2)
    result = classify_portfolio(
        results, _universe(), CURRENCIES, SPEND, portfolio_card_keys=["syn_ecom", "syn_flat"],
        assumptions=ASSUMPTIONS, candidate_card_keys=["syn_ecom", "syn_miles"],
    )
    assert {c.card_key for c in result.candidates} == {"syn_miles"}


def test_pv_falls_back_to_a_fresh_solve_when_not_in_results():
    # Only the two single-card results are supplied (no 2-card subset at
    # all) -- classify_portfolio must solve {syn_ecom, syn_flat} itself via
    # allocate()+repair() (SS E.8's "if enumerated; else one extra solve"),
    # landing on the exact same Rs17,850.00 hand computation as the
    # lookup-path test above.
    results = tuple(r for r in _enumerate(max_cards=2) if r.size == 1)
    result = classify_portfolio(
        results, _universe(), CURRENCIES, SPEND, portfolio_card_keys=["syn_ecom", "syn_flat"], assumptions=ASSUMPTIONS,
    )
    assert result.pv_exact == Decimal("17850.00")
    by_card = {c.card_key: c for c in result.owned}
    assert by_card["syn_ecom"].icv == Decimal("8400.00")


def _fabricated(card_keys, pv_exact):
    return SubsetResult(
        subset_key="+".join(sorted(card_keys)), card_keys=tuple(card_keys), size=len(card_keys),
        pv_planned=pv_exact, pv_exact=pv_exact, repair_applied=False, gap=Decimal("0"),
        allocation=None, card_results={},
    )


def test_close_and_hold_on_a_zero_or_negative_icv_card():
    # Fabricated combo where syn_miles adds a net-negative Rs250 to
    # syn_flat's own standalone value -- ICV(syn_miles|P) = 9,200.00 -
    # 9,450.00 = -250.00, isolating the CLOSE/HOLD branch (no real fixture
    # produces a negative-ICV card in this catalog).
    results = (
        _fabricated(("syn_flat",), Decimal("9450.00")),
        _fabricated(("syn_miles",), Decimal("1350.00")),
        _fabricated(("syn_flat", "syn_miles"), Decimal("9200.00")),
    )

    unflagged = classify_portfolio(
        results, _universe(), CURRENCIES, SPEND, portfolio_card_keys=["syn_flat", "syn_miles"], assumptions=ASSUMPTIONS,
    )
    by_card = {c.card_key: c for c in unflagged.owned}
    assert by_card["syn_miles"].icv == Decimal("-250.00")
    assert by_card["syn_miles"].label == CLOSE
    assert by_card["syn_flat"].label == KEEP  # ICV 7,850.00

    flagged = classify_portfolio(
        results, _universe(), CURRENCIES, SPEND, portfolio_card_keys=["syn_flat", "syn_miles"],
        assumptions=ASSUMPTIONS, strategic_feature_cards=frozenset({"syn_miles"}),
    )
    held = {c.card_key: c for c in flagged.owned}["syn_miles"]
    assert held.label == HOLD
    assert held.icv == Decimal("-250.00")
    assert "Rs250.00" in held.note


def test_downgrade_when_a_family_sibling_would_net_more_value():
    # Purely mechanical: pretend syn_miles is syn_ecom's family sibling and
    # fabricate a swapped-subset value that beats keeping syn_ecom.
    results = (
        _fabricated(("syn_ecom", "syn_flat"), Decimal("17850.00")),
        _fabricated(("syn_flat", "syn_miles"), Decimal("20000.00")),
    )
    result = classify_portfolio(
        results, _universe(), CURRENCIES, SPEND, portfolio_card_keys=["syn_ecom", "syn_flat"],
        assumptions=ASSUMPTIONS, family_keys={"syn_ecom": "famA", "syn_miles": "famA"},
    )
    by_card = {c.card_key: c for c in result.owned}
    assert by_card["syn_ecom"].label == DOWNGRADE
    assert by_card["syn_ecom"].downgrade_to == "syn_miles"
    assert by_card["syn_flat"].label == KEEP  # unaffected, no family link
