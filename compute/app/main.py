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

import psycopg
from dotenv import load_dotenv
from fastapi import Depends, FastAPI, HTTPException

load_dotenv()  # compute/.env's DATABASE_URL, local-dev convenience; a real
# deployment's own environment variables take precedence (load_dotenv()
# never overwrites an already-set variable).

from app.auth import AdminSession, get_admin_session  # noqa: E402
from app.repository import (  # noqa: E402
    CardNotFoundError,
    CardRepository,
    PostgresCardRepository,
    SyntheticCatalogRepository,
)
from app.schemas import (  # noqa: E402
    CardClassificationOut,
    CardDetailOut,
    CardSummaryOut,
    DraftCardStatusOut,
    EvaluateRequest,
    EvaluateResponse,
    ExcludedCardOut,
    FrontierPointOut,
    NextBestSpendRequest,
    NextBestSpendResponse,
    NextBestSpendResultOut,
    OptimiseRequest,
    OptimiseResponse,
    PublishCardVersionRequest,
    PublishCardVersionResponse,
    RecommendationStepOut,
    RejectSourceLinkRequest,
    ReviewQueueGroupOut,
    ReviewQueueItemOut,
    ReviewQueueResponse,
    RobustnessOut,
    SourceLinkActionResponse,
    SpendItemIn,
    spend_input_from_items,
)
from engine.card_bundle import CardRuleBundle  # noqa: E402
from engine.evaluate import EvaluateAssumptions, evaluate_card  # noqa: E402
from engine.normalise import SpendInput  # noqa: E402
from engine.valuation import RewardCurrency  # noqa: E402
from ingest.publish import PublishError, check_publish_gate, publish_card_version  # noqa: E402
from ingest.review import build_review_queue  # noqa: E402
from ingest.storage import StorageError, SupabaseStorageBackend  # noqa: E402
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


@app.get("/cards", response_model=list[CardSummaryOut])
def list_cards(repository: CardRepository = Depends(get_repository)) -> list[CardSummaryOut]:
    """Part F §F.2.1's Catalog screen, Slice 1 (F.8) -- public, no auth, no
    math: a thin wrapper over `CardRepository.list_card_summaries()`.
    Ordered by `card_key` (both repository implementations' own query/list
    order already), not re-sorted here."""
    return [CardSummaryOut.from_summary(s) for s in repository.list_card_summaries()]


@app.get("/cards/{card_key}", response_model=CardDetailOut)
def get_card(card_key: str, repository: CardRepository = Depends(get_repository)) -> CardDetailOut:
    """Part F §F.2.1's Catalog detail view -- the summary row plus the
    full rule breakdown read straight off the same `CardRuleBundle`
    `/evaluate` and `/optimise` already load a card into (`_jsonable`,
    `app/schemas.py`), not a second translation of it."""
    try:
        summary = repository.get_card_summary(card_key)
        bundle = repository.get_card_bundle(card_key)
    except CardNotFoundError:
        raise HTTPException(status_code=404, detail=f"unknown card_key {card_key!r}")
    return CardDetailOut.from_summary_and_bundle(summary, bundle)


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


def get_ingest_connection():
    """A connection separate from `get_repository()`'s cached
    `PostgresCardRepository` -- that repository's interface has no
    review/mutation methods (it exists for card-catalog reads only), and
    sharing its single long-lived connection across unrelated request
    transactions isn't worth the coupling. Opened per request and closed
    via this generator's own `finally` (FastAPI's standard yield-dependency
    cleanup idiom -- guaranteed to run even if the route body raises,
    unlike a manual `try/finally` duplicated in every route), same
    one-connection-per-invocation pattern `ingest/cli.py::_connect()`
    already uses for every DB subcommand -- immaterial at this endpoint's
    traffic (a single admin reviewing a handful of source_links at a
    time)."""
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        raise HTTPException(
            status_code=500,
            detail="DATABASE_URL is not configured -- Ingestion Review requires a live database.",
        )
    conn = psycopg.connect(database_url, prepare_threshold=None)
    try:
        yield conn
    finally:
        conn.close()


def _signed_snapshot_url(storage_path: str | None) -> str | None:
    """Part F §F.2.3's "at minimum a link to the stored snapshot, never
    the live URL" (Slice 6). `storage_path` is stored as `"<bucket>/
    <object_path>"` (`ingest/capture.py`'s own convention -- confirmed
    against the live catalog, not assumed); the `sources` bucket is
    private (`ingest/storage.py`'s own docstring), so a signed, time-
    limited URL is the only thing a browser can actually open. `None`
    in, `None` out -- a source captured before Part I's own capture
    tooling existed (or never captured at all) has nothing to link to,
    surfaced honestly rather than a broken link. A signing failure
    (misconfigured credentials, object deleted out from under the DB
    row) degrades the same way -- worth knowing about, never worth
    failing the whole review-queue request over one bad link."""
    if not storage_path:
        return None
    base_url = os.environ.get("SUPABASE_URL")
    service_role_key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
    if not base_url or not service_role_key:
        return None
    bucket, _, object_path = storage_path.partition("/")
    if not object_path:
        return None
    storage = SupabaseStorageBackend(base_url=base_url, service_role_key=service_role_key)
    try:
        return storage.create_signed_url(bucket, object_path, expires_in=3600)
    except StorageError:
        return None


def _list_draft_card_version_ids(conn: psycopg.Connection) -> list[str]:
    """Every DRAFT card_version, not just ones with unreviewed source_links
    -- a card_version that's already fully approved has NOTHING left in
    `build_review_queue`'s own output (Slice 6's own behavior: the group
    disappears once every item is reviewed), but F.2.3's Publish button
    still needs to appear for it. Ordered by card key for a stable,
    predictable render order."""
    with conn.cursor() as cur:
        cur.execute(
            "select cv.id from card_versions cv join cards c on c.id = cv.card_id"
            " where cv.status = 'draft' order by c.key",
        )
        return [str(row[0]) for row in cur.fetchall()]


@app.get("/review-queue", response_model=ReviewQueueResponse)
def review_queue(
    admin: AdminSession = Depends(get_admin_session),
    conn: psycopg.Connection = Depends(get_ingest_connection),
) -> ReviewQueueResponse:
    """Part F §F.2.3's Ingestion Review screen (Slice 6-7) -- a thin
    wrapper over `ingest/review.py::build_review_queue`'s own query
    (F.3) for the list/approve/reject half, plus `ingest/publish.py::
    check_publish_gate` (Slice 7) for the "ready to publish" indicator
    -- neither is a reimplementation, so the API's notion of "unreviewed"
    or "ready" can never drift from the CLI's. Auth-gated per F.6 --
    `admin` is unused beyond proving the dependency ran; the allow-list
    check itself is `get_admin_session`'s job, not this route's.

    Uses `conn.transaction()`, not a bare `with conn:` -- the latter
    calls `conn.commit()`/`conn.rollback()` AND `conn.close()` on the
    connection directly (confirmed by reading psycopg3's own
    `Connection.__exit__`), which would close the connection this
    request's `get_ingest_connection` dependency still owns (its own
    `finally` closes it again after the route returns) -- redundant in
    production (closing twice is harmless) but breaks the ability to
    test this route by overriding the dependency with a connection the
    TEST also wants to keep using afterward (`conn.transaction()`
    nests as a SAVEPOINT instead, never touching the connection's own
    lifecycle)."""
    del admin
    with conn.transaction():
        groups = build_review_queue(conn)
        draft_ids = _list_draft_card_version_ids(conn)
        draft_cards = []
        for cv_id in draft_ids:
            cur = conn.cursor()
            cur.execute("select bundle_path, golden_paths from card_versions where id = %s", (cv_id,))
            bundle_path, golden_paths = cur.fetchone()
            cur.close()
            report = check_publish_gate(conn, cv_id, golden_paths, bundle_path)
            draft_cards.append(DraftCardStatusOut.from_report(report))

    groups_out = [
        ReviewQueueGroupOut.from_group(
            group,
            [ReviewQueueItemOut.from_item(item, _signed_snapshot_url(item.source_storage_path)) for item in group.items],
        )
        for group in groups
    ]
    return ReviewQueueResponse(groups=groups_out, draft_cards=draft_cards)


@app.post("/source-links/{source_link_id}/approve", response_model=SourceLinkActionResponse)
def approve_source_link(
    source_link_id: str,
    admin: AdminSession = Depends(get_admin_session),
    conn: psycopg.Connection = Depends(get_ingest_connection),
) -> SourceLinkActionResponse:
    """Part F §F.4's narrow write path, verbatim: the ONLY way this
    endpoint ever mutates `source_links` is this one fixed `UPDATE`
    statement -- no column name ever comes from request input, `source_id`
    is never writable here at all. Directly responsive to docs/DECISIONS.md
    #158's corruption incident (see F.4's own detailed rationale).

    `conn.transaction()`, not `with conn:` -- see `review_queue`'s own
    docstring for why."""
    del admin
    with conn.transaction():
        with conn.cursor() as cur:
            cur.execute(
                "update source_links set reviewer_status = 'approved' where id = %s returning id",
                (source_link_id,),
            )
            row = cur.fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail=f"unknown source_link id {source_link_id!r}")
    return SourceLinkActionResponse(source_link_id=source_link_id, reviewer_status="approved")


@app.post("/source-links/{source_link_id}/reject", response_model=SourceLinkActionResponse)
def reject_source_link(
    source_link_id: str,
    request: RejectSourceLinkRequest,
    admin: AdminSession = Depends(get_admin_session),
    conn: psycopg.Connection = Depends(get_ingest_connection),
) -> SourceLinkActionResponse:
    """Same F.4 narrow-write-path posture as `approve_source_link` -- the
    one additional column this fixed statement ever touches is
    `previous_rule_note`, and only because F.2.3 requires a note to
    reject at all (`RejectSourceLinkRequest.note`, `min_length=1`).

    `conn.transaction()`, not `with conn:` -- see `review_queue`'s own
    docstring for why."""
    del admin
    with conn.transaction():
        with conn.cursor() as cur:
            cur.execute(
                "update source_links set reviewer_status = 'rejected', previous_rule_note = %s"
                " where id = %s returning id",
                (request.note, source_link_id),
            )
            row = cur.fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail=f"unknown source_link id {source_link_id!r}")
    return SourceLinkActionResponse(source_link_id=source_link_id, reviewer_status="rejected")


@app.post("/card-versions/{card_version_id}/publish", response_model=PublishCardVersionResponse)
def publish_card_version_endpoint(
    card_version_id: str,
    request: PublishCardVersionRequest,
    admin: AdminSession = Depends(get_admin_session),
    conn: psycopg.Connection = Depends(get_ingest_connection),
) -> PublishCardVersionResponse:
    """Part F §F.2.3/§F.4's guarded Publish button (Slice 7, docs/
    DECISIONS.md #170) -- the one irreversible action in this UI (Part D
    Decision 2). Calls `ingest.publish.publish_card_version` directly,
    the SAME code path `ingest publish` uses -- "no new publish logic,
    no new failure modes" (F.4's own explicit design constraint).
    `bundle_path`/`golden_paths` are read from the `card_versions` row
    itself (migrations 0003/0004, #169), never from request input -- a
    button click has no human typing `--bundle`/`--golden`.

    Two checks before the real mutation, neither optional:
    1. `confirm_card_key` must match the card's actual key -- F.2.3's
       "type the card's own key to confirm", re-verified server-side
       (the HTML form's own `pattern` attribute is a UX nicety, not the
       enforcement).
    2. `check_publish_gate` runs FIRST, read-only -- if it reports NOT
       ready, refuse with a clean 422 naming every problem, without
       ever calling `publish_card_version` (which would otherwise crash
       on a `None` `bundle_path` rather than refusing cleanly). This is
       deliberately a check-then-act sequence, not pure redundancy:
       `publish_card_version` still re-validates canonically before its
       own mutation, closing the race window between this check and
       the click actually landing.

    `conn.transaction()`, not `with conn:` -- see `review_queue`'s own
    docstring for why. `publish_card_version`'s own internal
    `conn.transaction()` nests as a SAVEPOINT under this one (the same
    nesting `tests/test_ingest_publish.py`'s own docstring documents
    and relies on), so the whole request -- gate check included --
    commits or rolls back as one atomic unit.
    """
    del admin
    with conn.transaction():
        cur = conn.cursor()
        cur.execute(
            "select c.key, cv.bundle_path, cv.golden_paths"
            " from card_versions cv join cards c on c.id = cv.card_id where cv.id = %s",
            (card_version_id,),
        )
        row = cur.fetchone()
        cur.close()
        if row is None:
            raise HTTPException(status_code=404, detail=f"unknown card_version id {card_version_id!r}")
        card_key, bundle_path, golden_paths = row

        if request.confirm_card_key != card_key:
            raise HTTPException(
                status_code=422,
                detail=f"confirmation key {request.confirm_card_key!r} does not match this card's key {card_key!r}",
            )

        try:
            report = check_publish_gate(conn, card_version_id, golden_paths, bundle_path)
        except PublishError as e:
            raise HTTPException(status_code=404, detail=str(e))
        if not report.passed:
            raise HTTPException(
                status_code=422,
                detail=f"publish gate failed for card_version {card_version_id} (card {card_key!r}): " + "; ".join(report.problems),
            )

        try:
            result = publish_card_version(conn, card_version_id, golden_paths or [], bundle_path)
        except PublishError as e:
            raise HTTPException(status_code=422, detail=str(e))

    return PublishCardVersionResponse.from_result(result)
