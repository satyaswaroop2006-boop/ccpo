"""CCPO compute service — FastAPI shell.

Endpoints land per Part E §E.0: /evaluate and /next-best-spend in Phase 3,
/optimise and /whatif in Phase 4. Handlers stay thin (parse request -> call
engine.evaluate.evaluate_card -> serialize) per CLAUDE.md rule 1 -- no
financial math here, only in compute/engine/.
"""
from decimal import Decimal
from functools import lru_cache

from fastapi import Depends, FastAPI, HTTPException

from app.repository import CardNotFoundError, CardRepository, SyntheticCatalogRepository
from app.schemas import (
    EvaluateRequest,
    EvaluateResponse,
    NextBestSpendRequest,
    NextBestSpendResponse,
    NextBestSpendResultOut,
    SpendItemIn,
    spend_input_from_items,
)
from engine.evaluate import evaluate_card

app = FastAPI(title="ccpo-compute", version="0.1.0")


@lru_cache
def get_repository() -> CardRepository:
    # Synthetic-catalog-backed for now (docs/DECISIONS.md's Phase 3 entry):
    # DATABASE_URL doesn't resolve from this dev sandbox, and there's no
    # local Postgres/Docker to verify a Postgres-backed repository against.
    # Swapping this factory is the entire migration once that's resolved.
    return SyntheticCatalogRepository()


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "engine": "phase 2 (11 stages + breakpoints), phase 3 (/evaluate, /next-best-spend)"}


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
