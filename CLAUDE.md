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
- [x] Phase 4 — optimiser (E.2–E.9, E.11–E.12), `/optimise`, repair pass.
      **Slice 1 done**: `optimiser/allocate.py` — the inner MILP for a
      *fixed* card subset (Part B SS B.2–B.4, Part E SS E.4), PuLP +
      HiGHS (CBC fallback verified). Continuous variables only (`x`, `s`)
      — no card-selection binary `y` (subset is a given input, per
      B.6/E.4), no milestone/waiver/fee/benefit-dedup binaries yet (docs/
      DECISIONS.md #68). Reward caps restricted to `scope="rule"` +
      monthly windows this slice (#70) — excludes nothing in the current
      12-card catalog, but `rule_group`/`card`-scoped and quarterly/annual
      reward caps raise rather than silently mismodel, same for
      `tier_mode="incremental"` cards (`syn_slab`). Verified against 3
      hand-computed scenarios incl. an exact match to
      `golden_syn_ecom_basic.json`.
      **Slice 2 done**: `optimiser/repair.py` (E.1 steps 5-6, E.7) —
      `evaluate_allocation` translates an `allocate()` solution into
      per-card spend and runs it through Phase 3's `evaluate_card`
      unchanged (pv_exact); `repair` walks `breakpoints.py`'s compiled
      threshold list (Phase 2, reused as-is) and tries topping under-
      threshold cards up from the outside option `c0` when within
      `buffer(β)`, keeping the exact-verified best. Near-miss only, not
      "barely-made" (already handled by the LP's own segment structure —
      #71/#72); top-up sourced from `c0` only, full-cover-or-nothing.
      **Slice 3 done**: `optimiser/enumerate.py` (E.3, subset generation)
      — pure orchestration over slices 1-2 (`itertools.combinations` +
      `allocate` + `repair` per subset), no new financial logic.
      `cardinality_mode` matches Part D's own vocabulary (`exactly`/
      `up_to`/`optimiser_decides`); `subset_key` matches
      `portfolio_subset_results`'s documented convention (sorted card
      keys, `+`-joined). Full-sweep only — no wallet-mode inclusion, no
      infeasibility filtering, no bound pruning, no caching, no
      parallelism (#73/#74, each blocked on machinery a later slice
      builds, not forgotten). Verified: `{syn_ecom, syn_flat}` enumerates
      exactly the 3 expected subsets, each cross-checked against slice 1's
      own hand-computed numbers, best-of-three correctly matches the
      2-card subset. 227/227 tests green.
      **Slice 4 done**: `optimiser/candidates.py` (E.2, pre-filtering) —
      builds Part B SS B.7's two-part coverage guarantee: standalone
      value (top-8 by `allocate`+`repair`'s exact single-card `pv_exact`
      — deliberately not a raw `evaluate_card` call, since a card's true
      standalone value can route negative-margin spend to `c0`) union
      per-category champions (top-2 by marginal rate, Part A SS A.15's
      `MV(c,k,Δ)` formula so the fixed annual fee cancels out of the
      comparison). No MABC (SS E.2's own addition beyond SS B.7, needs a
      "force spend to a tier" construct nothing builds) and no hard
      include/exclude from wallet/constraints (#75/#76, same blockers as
      #73). Verified: standalone ranking and category-champion ranking
      both match hand computation exactly; two scenarios with an
      artificially tight `standalone_n`/`max_total` demonstrate the
      union/trim mechanics concretely (a tight standalone cut still gets
      rescued by champions; trimming only ever removes a standalone-only,
      non-champion card). 231/231 tests green.
      **Slice 5 done**: `optimiser/frontier.py` (E.9, efficient frontier +
      the transparent size-recommendation checklist) + `optimiser/
      classify.py` (E.8, ICV/Overlap + KEEP/OPTIONAL/CLOSE/HOLD/ADD/
      DOWNGRADE). `build_frontier` groups enumerate.py's results by size,
      keeps the max-pv_exact winner per size, then walks consecutive
      steps against T1 (materiality, ≥max(₹2,000, 3%·V(n))), T2 (fee-cover,
      ΔGrossBenefit/ΔF≥1.5 or ΔF≤₹1,000 — ΔF read as a portfolio-total
      fee delta since the frontier gives no nesting guarantee, #79) and
      T3 (scenario floor — optional, `None`/"not evaluated" until
      `optimiser/scenarios.py` exists to supply low-spend numbers, #78).
      `classify_portfolio` computes ICV via lookup into an already-
      enumerated result set, falling back to one ad hoc `allocate()`+
      `repair()` solve when the exact subset wasn't swept (SS E.8's own
      "if enumerated; else one extra solve" clause) — verified this
      recovers the identical value a full sweep would give. DOWNGRADE
      and HOLD are spec-complete but have no real fixture yet: no card
      anywhere in the repo carries a `family_key` (Part D §D.3, never
      added to the schema) and no user-constraint model exists to derive
      a "strategic feature" flag from, so both are tested against
      directly-constructed data rather than the live catalog (#79).
      Verified end-to-end through the real engine where a fixture exists
      (syn_ecom+syn_flat frontier and 2-card ICV both match independent
      hand computations exactly) plus targeted unit tests for T2/T3/
      DOWNGRADE/HOLD's own arithmetic. 248/248 tests green.
      **Slice 6 done**: `optimiser/scenarios.py` (E.11, Low/Expected/High
      spend sweeps). `run_scenarios` re-runs `enumerate_subsets` at 0.8x/
      1.2x spend over the *same* candidate-card list the caller's
      expected-spend sweep already used (an `expected_results` param lets
      that sweep be reused rather than solved a third time); `scale_spend`
      multiplies both `CategorySpend.annual_amount` and
      `UpiAggregateSpend.monthly_amount` uniformly — per-category scenario
      editing is explicitly a later spec increment, not done here.
      `robustness_for` reports `Robustness = V_low/V_expected` (`None`,
      not a fabricated ratio, when a portfolio has no positive expected
      value to keep) and rank stability (does the portfolio stay in the
      top-3 across all three sweeps, ranked among every enumerated
      subset, not just same-size ones); `low_spend_pv_by_subset_key`
      packages the Low sweep straight into `frontier.build_frontier`'s
      T3 parameter. Verified end-to-end on two real fixtures (Low/
      Expected/High numbers match hand computation exactly at both
      Rs6,00,000/yr and Rs12,00,000/yr spend; the second one's Low sweep
      is fed live into `build_frontier` and confirmed to pass T3
      correctly) plus fabricated-data tests for the "drops out of top-3"
      and "V_expected<=0" edge cases no real fixture produces. 255/255
      tests green.
      **Slice 7 done**: `optimiser/explain.py` (E.12, explainability).
      Covers 4 of SS E.12's 5 surfaces — the 5th (marginal bands / Next-
      Best-Spend) is already `POST /next-best-spend` from Phase 3, not
      rebuilt. `build_card_ledger` groups a card's existing trace into
      SS37's reward/milestones/benefits/costs buckets (inherits #27's
      "reward is one lump line, not base/bonus split" gap — documented,
      not hidden). `threshold_funding_report` reports every threshold's
      funded/short status by reusing `repair.py`'s own pooling logic
      (`pooled_spend_per_instance`, promoted from private to public);
      deliberately doesn't cover cap-*binding* state (needs Stage 5
      internals nothing exposes yet, #93). `scan_driver`/
      `find_smallest_flip` (SS38 crossovers + "what could change this?")
      vary one spend line across a grid and re-solve both candidate
      portfolios via `allocate`+`repair` at each point — always a full
      re-solve, not a literal "evaluator only" shortcut, since freezing
      an old allocation's split would silently understate value once a
      card's segments saturate differently (#94). `marginal_value_curve`
      sweeps one card through the evaluator and annotates kinks from
      `breakpoints.py`'s compiled list — caught and fixed a real bug
      before it shipped: a monthly-window cap's breakpoint (e.g.
      "Rs20,000/month") has to be multiplied by its window's yearly
      instance count before comparing against an ANNUAL spend grid, or
      the kink marker lands nowhere near where the curve actually bends
      (#95, verified against 5 hand-computed curve points showing the
      real slope change at Rs2,40,000, not Rs20,000). No new assumption-
      registry defaults this slice — everything here reshapes numbers the
      engine already produces. 265/265 tests green.
      **Slice 8 done — Phase 4 complete**: `POST /optimise` (`app/
      main.py`), wiring candidates → enumerate → scenarios → frontier →
      classify into one endpoint. `CardRepository` gained
      `get_all_card_bundles()` (SS E.2's "live card universe" input,
      didn't exist before — both implementations). Discovered and fixed
      before shipping: feeding the FULL live catalog into candidate
      selection crashes it the moment ANY one card is
      allocate()-incompatible (3 of today's 12 synthetic cards are:
      `syn_points`/`syn_slab` are genuine `allocate.py` scope gaps #68/
      #70; `syn_lounge` just needs `benefit_need`/`benefit_unit_value`
      assumptions supplied). Fixed with `_partition_universe`, a
      pre-flight `allocate()`+`repair()` compatibility probe per universe
      card at the API layer only — `optimiser/candidates.py`/`allocate.py`/
      `repair.py` themselves are untouched and still raise exactly as
      before for direct callers; excluded cards are reported in the
      response with their reason (`excluded_cards`), never silently
      dropped (#97/#98). `candidate_universe` is an explicit override
      (`None` = full catalog) — also the seam wallet mode will extend
      later (#99). Response = frontier + size-recommendation checklist +
      ICV classification (owned + candidates) + robustness — explain.py's
      crossover/curve/ledger surfaces deliberately NOT bundled in (each
      needs its own per-query inputs a single response can't supply
      generically; left for future dedicated endpoints, #100). No
      persistence yet (`optimisation_runs`/`portfolio_subset_results`/
      `evaluation_runs`), same deferral as Phase 3's `/evaluate` (#101).
      Verified end-to-end via `TestClient`: reproduces test_frontier.py's
      and test_scenarios.py's own independently hand-verified numbers
      exactly through the full HTTP stack, plus a manual smoke run
      against the complete 12-card live catalog confirming the predicted
      3-card exclusion set and a clean end-to-end recommendation. 271/271
      tests green.

Phase 4's module list (Part E §E.0) is now fully built and wired.
- [~] Phase 5 — real card ingestion (Part I workflow), in progress.
      **Part I drafted** (docs/DECISIONS.md #103–107): `docs/Part_I_
      Ingestion_Workflow.md` didn't exist anywhere in the repo — only
      referenced by Parts C/D as if it did. Flagged to Satya rather than
      improvised; he asked for the document itself first. Covers: source
      capture (§I.1), the ingestion bundle format extending the
      *implemented* card-dict shape (§I.2, #104), extraction discipline
      binding Claude explicitly — never fill a field from memory, never
      self-approve a source (§I.0/§I.5, #107), the six-stage
      CAPTURE→DRAFT→LINT→LINK→REVIEW→PUBLISH pipeline (§I.4), confidence/
      reviewer_status semantics (§I.5), devaluation via Part D's existing
      new-`card_version` pattern (§I.6), golden coverage extended to real
      cards as a hard publish gate (§I.8, #106), and the intended
      `ingest lint/link/review-queue/publish` CLI shape (§I.9).
      **First real-card pipeline validation done**: Satya hand-drafted
      `compute/ingestion/bundle_sbi_cashback.json` (CASHBACK SBI Card,
      from the e-kit T&C + MITC) before any tooling existed — a
      deliberate validation exercise, not a bulk load. Wired against the
      engine directly, verified against a golden with two scenarios
      (steady-state annual passes exactly; the PDF's own worked example
      is a permanent, reasoned skip — EMI exclusion has no representation
      anywhere in the schema, #112). Both source PDFs fetched and read in
      full; all 6 items in the bundle's `_review_checklist` resolved —
      4 confirmed/approved by Satya with exact quotes, 2 were never
      source questions (#115–123).
      **`ingest lint` built — Part I §I.9's first tool** (#124–129):
      structural validation, no database access. Found and fixed a real
      bug on the way, not a design gap: `card_bundle.py`'s selector
      loaders silently dropped every C.2.1 selector field except the four
      the engine matches on, so the engine's own already-correct guards
      (`match.py`, `eligibility.py`) against unsupported fields
      (`mcc_include`, `txn_max`, etc.) never actually fired. Fixed at the
      loader (the root cause), not worked around three separate times;
      surcharges had no such guard *at all*, so one was added
      (`costs.validate_surcharge`). All three validators promoted to
      public so the lint tool reports *every* bad rule/exclusion in one
      run, not just the first one hit. Running the finished tool against
      the real SBI bundle found something new no manual review pass had
      caught — the currency/route carry no source citation at all — the
      argument for building the tool in the first place. `compute/ingest/`
      also had to reconcile a real spec-vs-practice gap: Part I §I.2
      specifies `source_refs` (a list); the one real bundle independently
      settled on `_source` (a string) — both now accepted rather than
      forcing a fourth edit of an already-approved artifact. 294/294
      tests green + 1 skipped. **Not built yet**: `ingest link`/`review-
      queue`/`publish` (touch Postgres) — no CLI stubs registered for
      them, since that would look like partial coverage of something
      that doesn't exist.
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
