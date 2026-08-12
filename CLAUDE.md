# CCPO — Credit Card Portfolio Optimiser (India)

Claude: read this file fully before doing anything in this repo.

## What this project is

A credit-card portfolio optimiser for the Indian market. The complete system
design lives in `docs/` and is the **source of truth** — code implements the
specs, never improvises past them:

- `docs/Part_A_B_...md` — financial model formulas + optimisation mathematics
- `docs/Part_C_...md`   — deterministic rules engine + JSON vocabulary
- `docs/Part_D_...md`   — database architecture (see `supabase/migrations/`)
- `docs/Part_E_...md`   — optimiser architecture, incl. module layout for
  `compute/` (§E.0) which this repo follows exactly

When a spec section is referenced in a task (e.g. "implement Stage 4 per
C.4"), open and follow that section. If the spec is ambiguous or seems wrong,
STOP and ask Satya — do not silently pick an interpretation. Log every such
decision in `docs/DECISIONS.md` (create on first use).

## Non-negotiable rules

1. **One engine.** Every rupee-valued number is computed in `compute/engine/`.
   No financial math in API handlers, the optimiser (it consumes engine
   outputs and effective rates), the frontend, or tests (tests assert against
   hand-computed constants, they don't re-derive).
2. **Goldens gate everything.** `compute/goldens/` holds golden scenarios with
   hand-computed expected values. Run `pytest` after EVERY change to
   `compute/engine/` or `compute/optimiser/`. A red golden is never "fixed" by
   editing its expected value unless the hand computation in its comment block
   is shown to be wrong — and then the comment must be corrected too.
3. **Published = immutable.** Never UPDATE published catalog rows (the DB
   enforces this with triggers). Rule changes are new `card_versions`.
4. **Synthetic vs real.** `seeds/synthetic_cards.py` cards are fictional test
   fixtures. NEVER add real card reward data from memory — real cards enter
   only via the Part I ingestion workflow with `sources` rows attached.
5. **Determinism.** Same inputs + same rule versions + same assumptions
   snapshot ⇒ byte-identical outputs. No wall-clock reads inside the engine
   (the modelling year is an input), no dict-ordering dependence, no floats
   where the spec says exact (use Decimal for money; see C.6 rounding modes).

## Build order (phases; do not skip ahead)

- [x] Phase 1 — scaffold, migration, synthetic seed
- [x] Phase 2 — engine Stages 1–11 (`compute/engine/`) + `breakpoints.py`
      (C.0's compile step) all implemented, 177/177 tests green. Golden
      coverage: 12 of 12 synthetic cards wired — full C.9 coverage
      (syn_ecom, syn_flat, syn_fuel, syn_lounge, syn_miles, syn_points,
      syn_renewal, syn_retro, syn_slab, syn_travel, syn_upi, syn_waiver).
      Open deferrals (none blocking): docs/DECISIONS.md, esp. #2 (UPI
      ticket-size question, never confirmed), #11/#32 (multi-category
      pooled cap/band windows), #19/#29 (RedemptionFees/WelcomeValue, no
      fixture), #27 (trace is a flat list, not C.10's full node schema),
      plus mcc/networks/txn/date selector fields still unsupported (only
      categories/channels/merchant_group/geography are).
- [x] Phase 3 — `POST /evaluate` + `POST /next-best-spend` built, tested,
      and live-wired to Postgres (214/214 tests green): `engine/
      card_bundle.py` (card-dict -> engine-dataclass loader, extracted
      from the golden battery's adapters, zero behaviour change) +
      `engine/evaluate.py` (`evaluate_card`, the Stage 1-11 pipeline
      composition, verified against all 12 goldens) + `app/schemas.py`/
      `app/repository.py`/`app/main.py`. `app/repository.py` has both
      `CardRepository` implementations: `SyntheticCatalogRepository`
      (seeds/synthetic_cards.py-backed) and `PostgresCardRepository`,
      built and verified against the live, already-seeded Supabase
      database once Satya supplied a working pooler connection string
      (docs/DECISIONS.md #62/#65) — 15 integration tests
      (`tests/test_postgres_repository.py`, auto-skipped when
      `DATABASE_URL` isn't set/reachable). `app/main.py`'s `get_repository`
      now defaults to `PostgresCardRepository` whenever `DATABASE_URL` is
      configured (#64/#66, resolved) — falls back to the synthetic catalog
      only when it's unset, raises loudly (not silently) if it's set but
      unreachable; `tests/test_api_evaluate.py` pins itself to the
      synthetic catalog via `app.dependency_overrides` so it stays fast
      and DB-independent regardless (#67). Live-verified: `POST /evaluate`
      against a running server reproduced `golden_syn_miles_vouchers.json`
      exactly, served from Postgres. **Not yet done**: `evaluation_runs`/
      `evaluation_traces` persistence. `/next-best-spend` is an annual
      marginal-delta MVP, not wallet mid-year state (#61, folded into
      #10's deferral) — the repair *pass* over the breakpoint compiler
      (already done, `breakpoints.py`) is separately Phase 4
      (`optimiser/repair.py` per E.0).
- [~] Phase 4 — optimiser (E.2–E.9), `/optimise`, repair pass. **Slice 1
      done**: `optimiser/allocate.py` — the inner MILP for a *fixed* card
      subset (Part B SS B.2–B.4, Part E SS E.4), PuLP + HiGHS (CBC
      fallback verified), 219/219 tests green. Continuous variables only
      (`x`, `s`) — no card-selection binary `y` (subset is a given input,
      per B.6/E.4), no milestone/waiver/fee/benefit-dedup binaries yet
      (docs/DECISIONS.md #68). Reward caps restricted to `scope="rule"` +
      monthly windows this slice (#70) — excludes nothing in the current
      12-card catalog, but `rule_group`/`card`-scoped and quarterly/annual
      reward caps raise rather than silently mismodel, same for
      `tier_mode="incremental"` cards (`syn_slab`). Verified against 3
      hand-computed scenarios incl. an exact match to
      `golden_syn_ecom_basic.json`. **Remaining build order** (#68):
      `optimiser/repair.py` (E.7, feeds `allocate`'s proposal to Phase 3's
      `evaluate_card`, repairs if gap > 2%) → `optimiser/enumerate.py`
      (E.3, subset generation) → `optimiser/candidates.py` (E.2,
      pre-filtering) → `optimiser/frontier.py` + `optimiser/classify.py`
      (E.8–E.9) → `optimiser/scenarios.py` (E.11) →
      `optimiser/explain.py` (E.12) + `POST /optimise`.
- [ ] Phase 5 — real card ingestion (Part I workflow)
- [ ] Phase 6 — frontend (Part F, to be authored)

Phase 2 was built stage by stage in pipeline order (C.4), one PR-sized
change per stage: normalise → eligibility → match → accrue → caps →
thresholds (two-pass, incl. activate_rule) → valuation → benefits → costs
→ assemble → breakpoints. Same incremental-with-goldens discipline applies
to Phase 3 onward.

## Working style with Satya

- Satya is a non-coder and reviews via golden outputs and worked examples,
  not code. After completing a task, show: what changed, which goldens/tests
  now pass, and one worked example in plain rupees.
- Surface every assumption-registry default you introduce; he signs those off.
- Prefer small verifiable steps over large batches. Never claim something
  works without having run it.
- Flag anomalies proactively (spec conflicts, surprising numbers, failing
  invariants) rather than working around them.

## Commands

```bash
# local database (dev)
createdb ccpo_dev && psql ccpo_dev -f supabase/migrations/0001_init.sql
cd compute && DATABASE_URL=postgresql://localhost/ccpo_dev python -m seeds.seed

# tests
cd compute && pytest            # unit + golden battery

# api (from Phase 3)
cd compute && uvicorn app.main:app --reload
```

Local dev note: the migration references Supabase's `auth` schema. For a
plain local Postgres, first run `supabase/local_auth_shim.sql` (creates a
minimal `auth.users` + `auth.uid()` + roles). Against a real Supabase
project, apply the migration as-is via the SQL editor or CLI.

## Environment

`compute/.env` (never commit): `DATABASE_URL` — local Postgres or the
Supabase connection string (service role for the engine; RLS applies to
user-facing keys only).
