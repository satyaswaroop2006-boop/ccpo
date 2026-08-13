"""CCPO compute service — FastAPI shell.

Endpoints land per Part E §E.0: /evaluate and /next-best-spend in Phase 3,
/optimise (this file's newest addition) and /whatif in Phase 4. Handlers
stay thin (parse request -> call engine/optimiser functions -> serialize)
per CLAUDE.md rule 1 -- no financial math here, only in compute/engine/ and
compute/optimiser/. `/optimise`'s own orchestration (candidate selection ->
enumeration -> scenarios -> frontier -> classification) is pure wiring
over already-built, already-tested optimiser modules; its only original
logic is `_partition_universe`, a pre-flight compatibility filter (see its
own docstring) -- not a rupee computation, so it stays here rather than in
compute/optimiser/.
"""
import os
from contextlib import asynccontextmanager
from decimal import Decimal
from functools import lru_cache

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, HTTPException

load_dotenv()  # compute/.env's DATABASE_URL, local-dev convenience; a real
# deployment's own environment variables take precedence (load_dotenv()
# never overwrites an already-set variable).

from app.repository import (  # noqa: E402
    CardNotFoundError,
    CardRepository,
    PostgresCardRepository,
    SyntheticCatalogRepository,
)
from app.schemas import (  # noqa: E402
    CardClassificationOut,
    EvaluateRequest,
    EvaluateResponse,
    ExcludedCardOut,
    FrontierPointOut,
    NextBestSpendRequest,
    NextBestSpendResponse,
    NextBestSpendResultOut,
    OptimiseRequest,
    OptimiseResponse,
    RecommendationStepOut,
    RobustnessOut,
    SpendItemIn,
    spend_input_from_items,
)
from engine.card_bundle import CardRuleBundle  # noqa: E402
from engine.evaluate import EvaluateAssumptions, evaluate_card  # noqa: E402
from engine.normalise import SpendInput  # noqa: E402
from engine.valuation import RewardCurrency  # noqa: E402
from optimiser.allocate import allocate  # noqa: E402
from optimiser.candidates import select_candidates  # noqa: E402
from optimiser.classify import classify_portfolio  # noqa: E402
from optimiser.enumerate import enumerate_subsets  # noqa: E402
from optimiser.frontier import build_frontier  # noqa: E402
from optimiser.repair import repair  # noqa: E402
from optimiser.scenarios import low_spend_pv_by_subset_key, robustness_for, run_scenarios  # noqa: E402


@asynccontextmanager
async def _lifespan(_app: FastAPI):
    yield
    repository = get_repository()
    if isinstance(repository, PostgresCardRepository):
        repository.close()


app = FastAPI(title="ccpo-compute", version="0.1.0", lifespan=_lifespan)


@lru_cache
def get_repository() -> CardRepository:
    """Postgres-backed when `DATABASE_URL` is configured (docs/
    DECISIONS.md #64) -- falls back to `SyntheticCatalogRepository` only
    when it's unset entirely. A `DATABASE_URL` that IS set but fails to
    connect raises loudly here (PostgresCardRepository's own `psycopg.
    connect` call), rather than silently masking a real misconfiguration
    behind fake catalog data -- a deployer who configured a database
    expects it to be used, not quietly skipped. Cached for the service's
    lifetime (one connection, not one per request); tests that need the
    synthetic catalog regardless of environment override this dependency
    directly (see `tests/test_api_evaluate.py`) rather than relying on
    `DATABASE_URL` being unset."""
    database_url = os.environ.get("DATABASE_URL")
    if database_url:
        return PostgresCardRepository(database_url)
    return SyntheticCatalogRepository()


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "engine": "phase 2 (11 stages + breakpoints), phase 3 (/evaluate, /next-best-spend), phase 4 (/optimise)"}


@app.post("/evaluate", response_model=EvaluateResponse)
def evaluate(request: EvaluateRequest, repository: CardRepository = Depends(get_repository)) -> EvaluateResponse:
    try:
        bundle = repository.get_card_bundle(request.card_key)
    except CardNotFoundError:
        raise HTTPException(status_code=404, detail=f"unknown card_key {request.card_key!r}")

    currencies = repository.get_currencies()
    spend = spend_input_from_items(request.spend)
    assumptions = request.assumptions.to_evaluate_assumptions()

    try:
        result = evaluate_card(bundle, currencies, spend, assumptions)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))

    return EvaluateResponse.from_result(result)


@app.post("/next-best-spend", response_model=NextBestSpendResponse)
def next_best_spend(request: NextBestSpendRequest, repository: CardRepository = Depends(get_repository)) -> NextBestSpendResponse:
    """Annual marginal-delta MVP (docs/DECISIONS.md's Phase 3 entry): for
    each (card, category) candidate and each Δ, the exact incremental
    steady-state NACV of adding Δ more annual spend on top of the baseline
    profile -- two full evaluate_card calls (baseline, baseline+Δ), no
    MILP, no wallet mid-year state. Results sorted best (highest net rate
    on the marginal rupee) first."""
    currencies = repository.get_currencies()
    results: list[NextBestSpendResultOut] = []

    for candidate in request.candidates:
        try:
            bundle = repository.get_card_bundle(candidate.card_key)
        except CardNotFoundError:
            raise HTTPException(status_code=404, detail=f"unknown card_key {candidate.card_key!r}")

        assumptions = candidate.assumptions.to_evaluate_assumptions()
        baseline_result = evaluate_card(bundle, currencies, spend_input_from_items(request.baseline_spend), assumptions)

        for delta in request.deltas:
            delta_item = SpendItemIn(
                category=candidate.category, annual_amount=delta, channel=candidate.channel,
                geography=candidate.geography, merchant_group=candidate.merchant_group,
            )
            delta_spend = spend_input_from_items([*request.baseline_spend, delta_item])
            delta_result = evaluate_card(bundle, currencies, delta_spend, assumptions)

            delta_nacv = delta_result.nacv.steady_state - baseline_result.nacv.steady_state
            results.append(NextBestSpendResultOut(
                card_key=candidate.card_key, category=candidate.category, delta=delta,
                baseline_nacv_steady_state=baseline_result.nacv.steady_state,
                delta_nacv_steady_state=delta_nacv,
                delta_nacv_rate=(delta_nacv / delta) if delta != 0 else Decimal("0"),
            ))

    results.sort(key=lambda r: r.delta_nacv_rate, reverse=True)
    return NextBestSpendResponse(results=results)


def _partition_universe(
    universe: list[CardRuleBundle],
    currencies: dict[str, RewardCurrency],
    spend: SpendInput,
    assumptions: EvaluateAssumptions,
) -> tuple[list[CardRuleBundle], list[ExcludedCardOut]]:
    """Pre-filters the live catalog to cards `optimiser.allocate.allocate` +
    `optimiser.repair.repair` can actually process for THIS request's
    spend/assumptions, before candidate selection ever sees them.

    Empirically, at today's 12-card synthetic catalog, 3 cards fail this
    probe with the default (empty) assumptions: `syn_points` (rule_group-
    scoped reward cap, docs/DECISIONS.md #68/#70 -- a genuine allocate.py
    scope gap, no request can work around it), `syn_slab` (incremental
    tier_mode, same #68/#70 gap), and `syn_lounge` (needs
    `benefit_need`/`benefit_unit_value` assumptions for its countable
    benefit -- NOT an allocate.py gap, just missing request configuration;
    supplying those assumptions makes it probe-compatible). Without this
    filter, `optimiser.candidates.select_candidates`'s own standalone-value
    loop (`allocate`+`repair` per universe card, unconditionally) would let
    ANY one incompatible card crash candidate selection for the ENTIRE
    catalog -- exactly the "one bad card sours everything" failure this
    exists to prevent. `candidates.py`/`allocate.py`/`repair.py` themselves
    are untouched: they keep raising exactly as before for any DIRECT
    caller (e.g. a hand-picked `candidate_universe` that still includes an
    incompatible card raises here too, just with a clear reason attached
    instead of an opaque request failure).

    Costs one extra `allocate`+`repair` solve per compatible card (worst
    case ~2x candidates.py's own standalone-value pass) -- immaterial at
    <=20 cards, not worth caching away this pass (see docs/DECISIONS.md).
    """
    compatible: list[CardRuleBundle] = []
    excluded: list[ExcludedCardOut] = []
    for bundle in universe:
        try:
            allocation = allocate([bundle], currencies, spend, assumptions)
            repair([bundle], currencies, allocation, assumptions)
        except ValueError as e:
            excluded.append(ExcludedCardOut(card_key=bundle.card_key, reason=str(e)))
        else:
            compatible.append(bundle)
    return compatible, excluded


@app.post("/optimise", response_model=OptimiseResponse)
def optimise(request: OptimiseRequest, repository: CardRepository = Depends(get_repository)) -> OptimiseResponse:
    """Part E SS E.1's flow, greenfield only (wallet mode: #10/#61, not
    built): CANDIDATES (E.2) -> ENUMERATE+ALLOCATE+EVALUATE+REPAIR (E.3-E.7,
    all inside `enumerate_subsets`) -> SCENARIOS (E.11, optional) ->
    ASSEMBLE (frontier E.9, classification E.8). No persistence
    (`optimisation_runs`/`portfolio_subset_results`/`evaluation_runs`) --
    same deferral as Phase 3's `/evaluate` (docs/DECISIONS.md's Phase 3
    status: "Not yet done: evaluation_runs/evaluation_traces persistence")."""
    currencies = repository.get_currencies()
    spend = spend_input_from_items(request.spend)
    assumptions = request.assumptions.to_evaluate_assumptions()

    if request.candidate_universe is not None:
        try:
            universe = [repository.get_card_bundle(k) for k in request.candidate_universe]
        except CardNotFoundError as e:
            raise HTTPException(status_code=404, detail=f"unknown card_key {e.args[0]!r}")
    else:
        universe = repository.get_all_card_bundles()

    compatible, excluded = _partition_universe(universe, currencies, spend, assumptions)
    if not compatible:
        raise HTTPException(
            status_code=422,
            detail="no candidate cards are compatible with the optimiser for this spend/assumptions; "
                   f"excluded: {[(c.card_key, c.reason) for c in excluded]}",
        )

    try:
        selection = select_candidates(
            compatible, currencies, spend, assumptions,
            standalone_n=request.standalone_n, champion_category_threshold=request.champion_category_threshold,
            champion_top_n=request.champion_top_n, champion_delta=request.champion_delta,
            max_total=request.max_total_candidates,
        )
        bundles_by_key = {b.card_key: b for b in compatible}
        bundles = [bundles_by_key[k] for k in selection.candidates]

        expected_results = enumerate_subsets(
            bundles, currencies, spend, assumptions,
            cardinality_mode=request.cardinality_mode, max_cards=request.max_cards,
        )

        sweep = None
        low_spend_map = None
        if request.run_scenarios:
            sweep = run_scenarios(
                bundles, currencies, spend, assumptions,
                cardinality_mode=request.cardinality_mode, max_cards=request.max_cards,
                expected_results=expected_results,
            )
            low_spend_map = low_spend_pv_by_subset_key(sweep)

        frontier = build_frontier(expected_results, bundles, n_tol=request.n_tol, low_spend_pv_by_subset_key=low_spend_map)

        results_by_key = {r.subset_key: r for r in expected_results}
        recommended_point = next(p for p in frontier.points if p.size == frontier.recommended_size)
        recommended = results_by_key[recommended_point.subset_key]

        classification = classify_portfolio(
            expected_results, bundles, currencies, spend,
            portfolio_card_keys=recommended.card_keys, assumptions=assumptions,
            candidate_card_keys=[k for k in selection.candidates if k not in recommended.card_keys],
            icv_meaningful=request.icv_meaningful,
            strategic_feature_cards=frozenset(request.strategic_feature_cards),
        )

        robustness_out = None
        if sweep is not None:
            robustness_out = RobustnessOut.from_robustness(robustness_for(recommended.subset_key, sweep))
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))

    return OptimiseResponse(
        candidates=list(selection.candidates),
        excluded_cards=excluded,
        frontier=[FrontierPointOut.from_point(p) for p in frontier.points],
        recommendation_steps=[RecommendationStepOut.from_step(s) for s in frontier.steps],
        recommended_size=frontier.recommended_size,
        capped_by_tolerance=frontier.capped_by_tolerance,
        recommended_subset_key=recommended.subset_key,
        recommended_card_keys=recommended.card_keys,
        recommended_pv_exact=recommended.pv_exact,
        classification_owned=[CardClassificationOut.from_classification(c) for c in classification.owned],
        classification_candidates=[CardClassificationOut.from_classification(c) for c in classification.candidates],
        robustness=robustness_out,
    )
