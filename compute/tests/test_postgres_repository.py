"""Live-database verification for `PostgresCardRepository` (docs/
DECISIONS.md #62-resolution). An integration test against a real seeded
Postgres instance, not part of the deterministic golden battery -- skipped
entirely when `DATABASE_URL` isn't set or isn't reachable, so it never
blocks `pytest` in an environment without database access (CLAUDE.md rule
2 still gates compute/engine/ itself; this only exercises the data-access
seam).

Structural bundle comparison against `SyntheticCatalogRepository` is done
on SORTED tuples (`earning_rules`/`caps`/`thresholds`/`exclusions`/
`surcharges`) rather than raw tuple equality: the DB has no reason to
return rows in the same order `seeds/synthetic_cards.py`'s Python list
declares them, and row order isn't semantically meaningful (CLAUDE.md rule
5 -- no ordering dependence), so this repository orders every query by
`key` for its own determinism rather than trying to reproduce declaration
order. `accruals`/`benefits` are dicts, already order-independent.

One expected, semantically inert difference: `redemption_routes.
friction_default` is `NOT NULL DEFAULT 1.0` in the schema, so the DB
always materializes `friction=Decimal("1.0")` for a route the Python
fixture leaves implicit (`friction=None`, meaning "use the engine's own
DEFAULT_FRICTION"). `engine/valuation.py::_route_value_per_point` treats
both identically -- proven below by running `evaluate_card` through both
repositories and asserting byte-identical NACV output, not just comparing
currency dataclasses field-by-field.
"""
import os
from decimal import Decimal

import pytest
from dotenv import load_dotenv

load_dotenv()
DATABASE_URL = os.environ.get("DATABASE_URL")

if DATABASE_URL:
    import psycopg

    try:
        psycopg.connect(DATABASE_URL, connect_timeout=5).close()
        DATABASE_REACHABLE = True
    except Exception:
        DATABASE_REACHABLE = False
else:
    DATABASE_REACHABLE = False

pytestmark = pytest.mark.skipif(not DATABASE_REACHABLE, reason="DATABASE_URL not set or not reachable")

from app.repository import CardNotFoundError, PostgresCardRepository, SyntheticCatalogRepository  # noqa: E402
from engine.evaluate import EvaluateAssumptions, evaluate_card  # noqa: E402
from engine.normalise import CategorySpend, SpendInput  # noqa: E402
from seeds.synthetic_cards import CARDS  # noqa: E402


@pytest.fixture(scope="module")
def pg_repo():
    repo = PostgresCardRepository(DATABASE_URL)
    yield repo
    repo.close()


@pytest.fixture(scope="module")
def syn_repo():
    return SyntheticCatalogRepository()


def test_all_twelve_synthetic_cards_are_seeded_and_readable(pg_repo):
    for card in CARDS:
        bundle = pg_repo.get_card_bundle(card["key"])
        assert bundle.card_key == card["key"]


def test_unknown_card_raises_card_not_found(pg_repo):
    with pytest.raises(CardNotFoundError):
        pg_repo.get_card_bundle("not_a_real_card")


@pytest.mark.parametrize("card", CARDS, ids=lambda c: c["key"])
def test_postgres_bundle_matches_synthetic_bundle(pg_repo, syn_repo, card):
    key = card["key"]
    pg_bundle = pg_repo.get_card_bundle(key)
    syn_bundle = syn_repo.get_card_bundle(key)

    assert pg_bundle.currency_key == syn_bundle.currency_key
    assert pg_bundle.joining_fee == syn_bundle.joining_fee
    assert pg_bundle.annual_fee == syn_bundle.annual_fee
    assert pg_bundle.forex_markup == syn_bundle.forex_markup
    assert pg_bundle.accruals == syn_bundle.accruals
    assert pg_bundle.benefits == syn_bundle.benefits
    assert sorted(pg_bundle.earning_rules, key=lambda r: r.key) == sorted(syn_bundle.earning_rules, key=lambda r: r.key)
    assert sorted(pg_bundle.caps, key=lambda c: c.key) == sorted(syn_bundle.caps, key=lambda c: c.key)
    assert sorted(pg_bundle.thresholds, key=lambda t: t.key) == sorted(syn_bundle.thresholds, key=lambda t: t.key)
    assert sorted(pg_bundle.exclusions, key=lambda e: e.key) == sorted(syn_bundle.exclusions, key=lambda e: e.key)
    assert sorted(pg_bundle.surcharges, key=lambda s: s.key) == sorted(syn_bundle.surcharges, key=lambda s: s.key)


def test_postgres_evaluate_card_matches_golden_syn_miles(pg_repo):
    # syn_miles: multi-route synth_points currency + voucher milestones --
    # the richest currency-valuation case among the 12 cards, so the
    # sharpest test of the friction=None-vs-1.0 representation difference
    # noted in the module docstring actually being inert.
    spend = SpendInput(category_spend=(
        CategorySpend(category="ecommerce", annual_amount=Decimal("480000")),
        CategorySpend(category="utilities", annual_amount=Decimal("420000")),
    ))
    assumptions = EvaluateAssumptions(
        primary_routes={"synth_points": "transfer"},
        voucher_utilisation=Decimal("1.0"), voucher_friction=Decimal("1.0"),
    )
    bundle = pg_repo.get_card_bundle("syn_miles")
    result = evaluate_card(bundle, pg_repo.get_currencies(), spend, assumptions)

    assert result.gross_reward_value == Decimal("14400.00")
    assert result.milestone_value == Decimal("20000.00")
    assert result.nacv.steady_state == Decimal("22600.00")
    assert result.nacv.year_1 == Decimal("10800.00")
    assert result.nacv.three_year == Decimal("56000.00")
