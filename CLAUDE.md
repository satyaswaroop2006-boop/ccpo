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

- [x] Phase 1 — scaffold, migration, synthetic seed  (this commit)
- [ ] Phase 2 — engine Stages 1–11 (`compute/engine/`), golden battery green
- [ ] Phase 3 — `/evaluate`, `/next-best-spend`, breakpoint compiler
- [ ] Phase 4 — optimiser (E.2–E.9), `/optimise`, repair pass
- [ ] Phase 5 — real card ingestion (Part I workflow)
- [ ] Phase 6 — frontend (Part F, to be authored)

Within Phase 2, build stage by stage in pipeline order (C.4), one PR-sized
change per stage: implementation + unit tests + any golden that becomes
runnable. Suggested granularity: normalise → eligibility → match → accrue →
caps → thresholds (two-pass) → valuation → benefits → costs → assemble.

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
