"""CCPO compute service — FastAPI shell.

Endpoints land per Part E §E.0: /evaluate and /next-best-spend in Phase 3,
/optimise and /whatif in Phase 4. Handlers stay thin (parse request -> call
engine.evaluate.evaluate_card -> serialize) per CLAUDE.md rule 1 -- no
financial math here, only in compute/engine/.
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
    EvaluateRequest,
    EvaluateResponse,
    NextBestSpendRequest,
    NextBestSpendResponse,
    NextBestSpendResultOut,
    SpendItemIn,
    spend_input_from_items,
)
from engine.evaluate import evaluate_card  # noqa: E402


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
