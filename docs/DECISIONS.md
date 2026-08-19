# Decisions log

Per CLAUDE.md: when a spec section is ambiguous, or a construct requires an
engine-level judgment call the spec doesn't pin down, it's logged here
instead of silently picked. New assumption-registry defaults are flagged
here too, for Satya's sign-off.

## Status as of 2026-08-15 (Phase 5 -- `ingest lint` built, Part I SS I.9's first tool)

Started building Part I SS I.9's tooling with `ingest lint` (structural
validation, no database access -- SS I.11's own stated build order:
loader/dataclass, then LINT, then LINK/PUBLISH). Found and fixed a real
bug on the way: `engine/card_bundle.py`'s selector loaders silently
dropped every Part C SS C.2.1 selector field except the four the engine
actually matches on, which meant the engine's OWN existing guards against
unsupported fields (`match.py`, `eligibility.py`) never actually fired --
not a design gap, a loader bug undermining validators that were already
built correctly. Fixed at the root (the loader), not worked around;
surcharges turned out to have no such guard AT ALL (a second, separate
gap), so one was added. All three validators promoted to public so the
new lint tool can call them per-item and report every issue in one run,
not just the first. Running the finished tool against
`bundle_sbi_cashback.json` surfaced one genuinely new finding no prior
manual review pass had caught (the currency/route carry no source
citation at all) alongside mechanically confirming the two exclusions
issues that were previously only hand-documented. `compute/ingest/`'s
own bundle-loading layer also had to reconcile a real spec-vs-practice
divergence: Part I SS I.2 specifies `source_refs` (a list); the one real
bundle uses `_source` (a string) -- both are now accepted rather than
forcing a fourth edit of an already-approved artifact. 294/294 tests
green + 1 skipped (16 new for the lint tool, 6 new engine-hardening
regression tests). See the dated entry below for full detail.

## Status as of 2026-08-15 (Phase 5 -- Rs.99 finding approved; all 6 checklist items now resolved)

Satya reviewed the Rs.99 Rewards Redemption Fee proposed reading and
approved it: the fee does NOT apply to CASHBACK SBI Card. Unlike the
rent-inclusion approval (#119/#120), this finding has no existing bundle
entity to attach `_reviewer_status` to -- correctly, since nothing models
this fee (there's nothing TO approve a field on; the approval is of an
absence, not a fact about a present field). Recorded directly on the
finding itself in `_review_findings.checklist_item_6_rs99_redemption_
fee`. All 6 items in `_review_checklist` are now resolved (4 confirmed/
approved with evidence, 2 were never source questions) -- see the dated
entry below.

## Status as of 2026-08-15 (Phase 5 -- Rs.99 redemption-fee question investigated)

Re-examined both cached source documents specifically for the open Rs.99
"Rewards Redemption Fee" question (not re-fetched -- both were already
read in full during the earlier findings pass). Found three converging
points that weren't connected before: the MITC's own fee entry defers to
"the individual product Terms & Conditions" for applicability; CASHBACK's
own T&C (unusually thorough on every other fee/timing/forfeiture detail)
never mentions this fee; and CASHBACK's own FAQ 14 states cashback
crediting requires no cardholder action at all ("automatically
credited"), which is incompatible with a fee that by definition attaches
to an active redemption choice. Recorded as a PROPOSED reading ("does not
apply") in the bundle's `_review_findings`, explicitly not marked
approved -- same two-step process as the rent-inclusion finding: Claude
proposes with reasoning, Satya decides. See the dated entry below.

## Status as of 2026-08-15 (Phase 5 -- first per-entity reviewer approval recorded)

Satya reviewed the rent-inclusion finding (checklist item 2) and
approved it. Recording that approval surfaced a real gap in the bundle
file format: `_sources.*.reviewer_status` is per-SOURCE, but the MITC
source backs two independent facts (the fee-waiver threshold, now
approved, and the still-open Rs.99 question) that don't share one
approval state -- exactly the granularity Part D's real `source_links`
table already has (per `(source, entity)`, never per bare source) and
the bundle file's shortcut didn't. Fixed by adding `_reviewer_status`/
`_reviewer_note` directly on the approved entity (`thresholds[0]`)
rather than flipping the source-level flag, which would have overclaimed
approval onto the unresolved Rs.99 question. `_sources.mitc.
reviewer_status` stays `unreviewed`. See the dated entry below.

## Status as of 2026-08-15 (Phase 5 -- source checklist reviewed against primary documents)

Fetched and read both cited sources in full (`reward_terms` -- the 47pp
e-kit T&C; `mitc` -- the 57pp MITC) and checked every item in the
bundle's `_review_checklist` against the actual text, plus cross-checked
everything else in the bundle opportunistically now that both documents
were in hand. Checklist items 1-3 confirmed (exact quotes recorded in the
bundle's new `_review_findings` block); items 4-5 were never source
questions to begin with. One NEW open question surfaced that wasn't on
the original checklist (MITC's Rs.99 "Rewards Redemption Fee" --
possibly applicable to CASHBACK's statement credit, possibly not,
genuinely ambiguous from the text). `reviewer_status` on both
`_sources` entries left `unreviewed` throughout -- per Part I SS I.0/I.5
(not yet signed off, but held to anyway), gathering and presenting
evidence is not the same act as approving it, and approval stays
human-only. See the dated entry below for full detail.

## Status as of 2026-08-15 (Phase 5 -- first real-card pipeline validation)

Satya hand-drafted the first real ingestion bundle (`compute/ingestion/
bundle_sbi_cashback.json`, CASHBACK SBI Card, from the e-kit T&C + MITC)
plus its golden (`golden_sbi_cashback.json`) *before* Part I's own tooling
(SS I.9) exists -- a deliberate pipeline-validation exercise, not the
first bulk load. Wired against the engine directly (`tests/
test_golden_sbi_cashback.py`), NOT published, NOT written to Postgres.
Found real schema-fit gaps (below) exactly as Part I SS I.0 anticipated
someone would; none were silently worked around. 273/273 tests green + 1
deliberately skipped (Scenario A, a genuine schema gap, not a bug). See
the dated entry below for full detail; this is the first empirical test
of Part I's own discipline, not just its prose. Satya then asked for the
bundle/golden files themselves to be renamed to match the loader's
naming (#113) -- done directly (both files edited in place this time,
unlike the earlier test-only-adapter approach), same 273/273 + 1 skipped
afterward, confirming the rename changed no numbers.

## Status as of 2026-08-13 (Phase 5 kickoff -- Part I drafted, awaiting sign-off)

**Phase 5 is blocked on approval, by design.** `docs/Part_I_Ingestion_
Workflow.md` did not exist anywhere in the repo -- CLAUDE.md rule 4 and
Part C SS C.9 both reference "the Part I ingestion workflow" as if it
already existed, but it was never authored. Per CLAUDE.md's own
instruction ("if the spec is ambiguous or seems wrong, STOP and ask
Satya"), this was flagged before writing any Phase 5 code; Satya asked
for the document itself to be drafted first, for his review, with no
`compute/` code following until it's approved. See the dated entry below
for what's in it and the judgment calls made while drafting. **No
ingestion code exists yet** -- Part I SS I.9 specifies the intended
tooling shape (`ingest lint/link/review-queue/publish`) as a target for
the next task, once the document itself is signed off.

## Status as of 2026-08-13 (Phase 4 complete -- POST /optimise)

Phase 2 complete: all 11 engine stages + `breakpoints.py` implemented,
177/177 tests green, 12/12 synthetic cards have a passing golden -- full
C.9 coverage. Phase 3 complete end-to-end: `engine/evaluate.py`'s
`evaluate_card` orchestrator, `POST /evaluate` and `POST /next-best-spend`
wired in `app/main.py`, live-backed by `PostgresCardRepository`. Phase 4
in progress: `optimiser/allocate.py` (slice 1, the inner MILP for a fixed
card subset) + `optimiser/repair.py` (slice 2, exact evaluation +
near-miss threshold repair) + `optimiser/enumerate.py` (slice 3, subset
generation over both) + `optimiser/candidates.py` (slice 4, pre-filtering
the card universe down to enumeration's input) + `optimiser/frontier.py`
(slice 5a, SS E.9 efficient frontier + the transparent size-recommendation
checklist) + `optimiser/classify.py` (slice 5b, SS E.8 ICV/Overlap +
KEEP/OPTIONAL/CLOSE/HOLD/ADD/DOWNGRADE) + `optimiser/scenarios.py`
(slice 6, SS E.11 Low/Expected/High spend sweeps, Robustness, rank
stability, feeds frontier.py's T3) + `optimiser/explain.py` (slice 7, SS
E.12 explainability: why-this-card ledger, threshold funding analysis,
crossover scans, marginal value curves) + `POST /optimise` (slice 8, the
wiring layer over every module above: candidates -> enumerate -> scenarios
-> frontier -> classify) -- 271/271 tests green. No blocking deferrals --
17 non-blocking items remain open (table below); none of them gate this
work. **Phase 4 is complete** -- every module in Part E SS E.0's layout
now exists and is wired to an endpoint. Next up: Phase 5 (real card
ingestion, Part I) or Phase 6 (frontend, Part F) -- Satya's call.

**Genuinely open items** (none blocking today's work; listed so a future
session doesn't have to scan all entries below to find them):

| # | Item | Why it's still open |
|---|---|---|
| #2 | C.7's "UPI small-ticket ₹350" ticket-size row | Never confirmed which reading is correct (category ticket vs. flat ₹350 for UPI-channel segments) |
| #11, #43 | Multi-category pooled cap / incremental-band windows | Raise rather than guess an attribution scheme; no fixture needs one yet |
| #9, #32 | Spend-measure caps: only `scope="rule"` supported | `syn_slab` only needs `rule` scope; `rule_group`/`card`-scoped spend-measure caps untested and unimplemented |
| #12, #39 | Caps × `activate_rule` interaction | Identity-based splice + Stage-5-before-Stage-6/7 ordering wouldn't reconcile if a future rule combines both; no current card does |
| #19 | Flat per-currency `RedemptionFees` (A.12) | Not modelled anywhere; no route in the seed catalog carries one |
| #27 | Explanation trace is a flat line-item list | Not C.10's full per-node schema (`cap_state`, per-currency `v`/`phi`, `source_refs`) — a real fidelity gap, flagged for a follow-up pass if §37/§74 need it soon |
| #29 | `WelcomeValue` has no real fixture | Parameter exists in `assemble_nacv`, always 0 in practice — no card/schema payload type for welcome bonuses |
| #10, #61 | True anniversary alignment; wallet mid-year state (`current_year_progress`) | Approximated as calendar-aligned; needs `card_anniversary_month` AND a way to seed already-triggered threshold/cap state, neither built (wallet mode itself doesn't exist yet) |
| #68 | `optimiser/allocate.py`: milestones/waiver/fees/benefit-dedup/card-selection/incremental-tier/rule_group-and-card-scoped-caps/quarterly-and-annual-reward-caps | This slice is continuous-variables-only (B.2's `x`,`s`); every binary-needing mechanic and every cap shape beyond scope="rule"+monthly-window is the explicit next increment, not silently dropped -- see #70 |
| #71 | `optimiser/repair.py`: no "barely-made" variants, no cap breakpoints, top-up sourced from `c0` only | Each is a real design surface deferred, not an oversight -- barely-made needs a cost model for what a card's excess spend gives up elsewhere; cap breakpoints are already optimal by construction in `allocate.py`'s LP; pulling top-up from a real card (not just `c0`) needs the same cost model barely-made does -- see #72 |
| #73 | `optimiser/enumerate.py`: no wallet-mode inclusion, no infeasibility filtering, no bound pruning, no caching, no parallelism | Full-sweep only this slice -- wallet mode and user-constraint machinery (must-keep/refuse/fee-budget) don't exist anywhere yet; bound pruning needs SS E.2's MABC ceiling (not built) and is explicitly a scale optimisation the spec says is irrelevant below the current <=12-card catalog; caching needs DB persistence nothing writes yet -- see #74 |
| #75 | `optimiser/candidates.py`: no MABC, no hard include/exclude, no `constraints_snapshot` persistence | MABC is SS E.2's own addition beyond SS B.7's core spec (SS B.7 doesn't mention it), needing a "force eligible spend to a tier" construct that doesn't exist; hard include/exclude needs wallet mode and user constraints (same blocker as #73); persistence needs DB writes nothing does yet -- see #76 |
| #78 | `optimiser/frontier.py`: T3 (scenario floor) only runs when the caller supplies low-spend pv_exact data | `optimiser/scenarios.py` (SS E.11, the Low/Expected/High sweep) is later in the build order than this slice; `t3_pass` is `None` ("not evaluated"), never defaulted to pass or fail, until scenarios.py exists and a caller wires it through |
| #79 | `optimiser/classify.py`: DOWNGRADE untested against a real fixture; no eligibility filter on ADD | `cards.family_key` (Part D SS D.3) doesn't exist anywhere in the schema yet -- grep-confirmed; DOWNGRADE is spec-complete and unit-tested against fabricated data only. ADD has no SS33 eligibility check (not modelled in the optimiser at all yet) -- ranked on ICV alone |
| #86 | `optimiser/scenarios.py`: uniform scalar scaling only, no literal sweep caching | SS E.11 itself defers per-category scenario editing to later; sweep-level caching is still blocked on the same DB-persistence gap as #73/#74 -- each of the 3 sweeps is a fresh full solve, just over a shared candidate set |
| #93, #94 | `optimiser/explain.py`: no cap-binding-state in `threshold_funding_report`; crossover scans always re-solve (no literal "evaluator only" shortcut for multi-card portfolios) | Cap `cap_state` (bound/unbound) isn't returned anywhere in the engine (same gap as #27); re-solving via `allocate`+`repair` is the only way to get a genuinely correct value as spend shifts across a multi-card portfolio, so SS E.12's "no MILP" is read as applying only to the single-card case it illustrates |
| #97 | `POST /optimise`: no persistence, no wallet mode, no explain.py wiring, 3/12 synthetic cards excluded by default | `optimisation_runs`/`portfolio_subset_results`/`evaluation_runs` writes don't exist yet (same gap as Phase 3's `/evaluate`); wallet mode itself doesn't exist (#10/#61); explain.py's crossover/curve/ledger surfaces need per-query driver/grid inputs a single "optimise" response can't supply generically -- left as separate future on-demand endpoints; `syn_points`/`syn_slab` are genuine `allocate.py` scope gaps (#68/#70), `syn_lounge` just needs `benefit_need`/`benefit_unit_value` assumptions supplied |
| — | `mcc_include`/`mcc_exclude`/`networks`/`txn_min`/`txn_max`/`date_from`/`date_to` selector fields | Still rejected everywhere selectors are matched (match.py, eligibility.py) — only categories/channels/merchant_group/geography are supported |

**Confirmed and settled (not open)**: `upi_category_mix` weights (#1),
`activate_rule` prospective/retroactive (#13, resolved #36-40), `syn_slab`
fill-order for `rule`-scope bands (#9, resolved #41-45, #45-update),
geography-aware selectors (#4, resolved #51-54), `PV` = NACV for a single
card, not a separate output (#28), `CategorySpend.merchant_group` input
path (#5, resolved #55-56), `condition: "on_renewal"` year-mode filtering
(#14, resolved #59-60), `PostgresCardRepository` built/verified/live-wired
(#62/#64, resolved #65-67). All 12/12 synthetic cards now have a golden.
`/evaluate` + `/next-best-spend` MVP scope confirmed with Satya (#63):
synthetic-catalog-backed for now, annual marginal-delta (not wallet
mid-year) for Next-Best-Spend. `PostgresCardRepository` built and verified
against the live database (#62, resolved below).

---

## 2026-08-11 -- Stage 1 (normalise.py), Part C SS C.4.1

### 1. `upi_category_mix` default weights -- NEW ASSUMPTION, needs sign-off

C.4.1 says the UPI aggregate decomposes "using the registry's
`upi_category_mix` default (grocery-heavy; editable)" but Part C never
states the actual weights -- C.7's registry section doesn't list them either.
I introduced a default (`engine/normalise.py::DEFAULT_UPI_CATEGORY_MIX`):

| Category | Weight |
|---|---|
| Grocery | 38% |
| Ecommerce | 15% |
| Dining | 12% |
| Utilities | 10% |
| Offline retail | 10% |
| Fuel | 8% |
| Entertainment | 7% |

Grocery-heavy per the spec's steer, shaped loosely around typical Indian
UPI P2M usage (kirana/grocery dominant, followed by food & ecommerce). This
is a guess, not sourced data -- please review and edit.

### 2. C.7's "UPI (small-ticket): Rs 350" row -- left unused, flagging the ambiguity

C.7's ticket-size table has a row "UPI (small-ticket) -- Rs 350" alongside
the category rows (Grocery Rs 700, Dining Rs 600, etc.). But C.4.1's
decomposition maps the UPI aggregate into real spend categories (grocery,
ecommerce, ...) via `upi_category_mix` -- there is no category literally
named "UPI" for that row to attach to once decomposition happens.

Two readings:
  (a) The row is vestigial/unused once decomposition maps to real
      categories -- each UPI-derived segment uses its *category's* normal
      ticket size (grocery UPI spend uses Rs 700, same as non-UPI grocery).
  (b) UPI-channel segments should use Rs 350 as the ticket size *regardless
      of category*, reflecting that UPI transactions run smaller than
      card-swipe transactions even within the same category (a UPI kirana
      payment vs. a supermarket card swipe).

**Implemented (a)** for Stage 1, since C.4.1 only specifies channel tagging
(`channel: upi, decomposition: assumed`) and category decomposition, not a
ticket-size override. Reading (b) would matter to Stage 4's rounding maths
(smaller ticket = more rounding loss per rupee) -- if that's the intent,
this needs a one-line change: UPI-channel segments look up a fixed Rs 350
instead of `ticket_sizes[category]`. **Please confirm which reading is
correct.**

### 3. Rounding/reconciliation convention (implementation detail, not a
business assumption -- noted for the audit trail, not sign-off)

Every allocation (month split, category split) must sum exactly to its
input total -- no rupee created or lost. Where proportional rounding leaves
a paisa-level residual, it's pushed onto the *last slot with a nonzero
weight* (last month with spend, or last category in the UPI mix). This is
an arbitrary-but-deterministic choice among several valid ones (e.g.
largest-remainder distribution); it only ever affects the last paisa or two
of a monthly figure, never the annual total.

---

## 2026-08-11 -- Stage 3 (match.py), Part C SS C.2.1 / SS C.2.6

### 4. Geography-scoped selectors not yet matchable -- gap, not a choice

C.9 Example 8 (syn_travel) has an earning rule selector
`{"geography": "international"}` (2x points on international spend). Stage
1's category-mode grid (category, channel, month, amount) carries no
geography dimension, so Stage 2 (eligibility.py) and Stage 3 (match.py)
both reject any selector naming `geography` (or `mcc_include`/`mcc_exclude`/
`networks`/`txn_min`/`txn_max`/`date_from`/`date_to`) rather than silently
treating it as "always matches" or "never matches" -- either would produce
wrong numbers without saying so.

This means syn_travel's "intl" rule cannot be evaluated yet. It isn't
blocking today's Stage 3 scope (syn_points/syn_ecom/syn_fuel don't need
geography), but it **will** block the golden battery once syn_travel needs
a golden scenario, and it will block real international-spend cards during
Phase 5 ingestion. Fix is additive when it's needed: give `CategorySpend`
(Stage 1) an optional `geography` field the same way `channel` and
`merchant_group` work today, thread it onto `SpendSegment`, then Stage 2/3
can drop `geography` from their unsupported-field lists. Flagging now so
it's on the record before it becomes a blocker.

**RESOLVED 2026-08-12** -- see entries #51-54 below. `golden_syn_travel_forex.json`
now exercises the "intl" rule end-to-end.

### 5. `SpendSegment.merchant_group` -- new field, Stage 1 doesn't populate it yet

Stage 3 needs to match `merchant_groups` selectors (syn_points' portal
bonus, C.9 Example 3), but Stage 1's `SpendSegment` only carried category/
channel/month/amount/ticket_size. Added `merchant_group: str | None = None`
to the dataclass (backward compatible -- all existing construction is by
keyword, confirmed by grep before editing; Stage 1/2's 24 existing tests
still pass unchanged).

`normalise()` itself is unchanged and does not populate this field --
there's no input path yet for a user to declare "Rs X of my spend was at
merchant group Y" the way `CategorySpend.channel` lets them declare a
channel. Stage 3's own tests construct `SpendSegment` directly with
`merchant_group` set, bypassing `normalise()`, the same pattern Stage 2's
tests already used for fields Stage 1 doesn't produce. Wiring a real input
path through Stage 1 is deferred until a card actually needs it end-to-end
through the full pipeline (same "additive when needed" posture as #4).

**RESOLVED 2026-08-12** -- see entries #55-56 below. `golden_syn_points_
portal_stacking.json` now exercises `CategorySpend.merchant_group` end-to-end.

---

## 2026-08-11 -- Stage 4 (accrue.py) + minimal caps.py, Part C SS C.2.2 / SS C.6

### 6. `caps.py` built early, deliberately narrow -- confirmed with Satya

`golden_syn_ecom_basic.json`'s own `purpose` field says it's a cap-binding
golden (Master Prompt SS55 test 1) -- its expected Rs14,400 gross figure is
only reachable after the Rs1,000/month cap on the "ecom" rule binds and
Rs10,000/month of overflow spend re-rates at the base 1% rate. That's
Stage 5's job, not Stage 4's, so making this golden green today meant
building a slice of Stage 5 ahead of schedule.

Satya confirmed: build a minimal `caps.py` now (just enough for this
golden), not the full Stage 5. What's implemented:
  - measure = "reward" only (not "spend")
  - window = "calendar_month" only (not quarter/annual)
  - scope = "rule" only (not "rule_group:<key>" or "card")
  - exactly one segment per rule per month (raises otherwise)
  - overflow "base_rate" resolved by re-running Stage 3's match with the
    capped rule excluded, taking whichever non-stacking rule wins -- not
    a hardcoded "base" rule lookup, so it generalises past a rule literally
    named "base"

Real Stage 5 (as its own task) needs: spend-measure caps, quarter/annual
windows with the C.2.4 clock resolver, rule_group and card-scope caps
(shared caps across several rules -- syn_points' actual cap_portal is
scope="rule_group:portal_accel", which this slice does NOT support yet),
and multi-segment months. `apply_caps`'s `_validate_cap` raises clearly on
all of these rather than silently mishandling them.

### 7. Bug found and fixed while building this: `caps.py` index-shift corruption

First version of `apply_caps` mutated its `results` list in place via
`results[idx:idx+1] = replacement` inside a loop over months, using
indices computed once before any mutation started. Each capped month
inserts an extra row (the overflow entry), shifting every later month's
precomputed index by one -- so month 2 onward silently capped/read the
WRONG list slot after month 1 was processed. Caught because the golden
came back Rs16,800 instead of Rs14,400 (months 7-12 ended up fully
uncapped: 1,600/mo instead of 1,200/mo).

Fixed by computing every cap decision against the original, untouched
`accrual_results` list first (a dict of index -> new reward, plus a
separate list of overflow entries), and only building the final result
list in one non-mutating pass at the end. Regression coverage: the golden
test itself now exercises all 12 months, and `test_caps.py` tests the
single-month math directly.

### 8. Materiality/`ea` (ticket-size effective rate) denominator convention

C.6 says the 1% materiality check is on "the rule's reward" without
specifying which of the two candidate values (ticket-approximated vs
unrounded) is the denominator -- they're close enough (A.2's own worked
example: ~6.67% one way, ~6.25% the other) that it doesn't change any
flagging decision so far, but noting the choice: implemented as
`|approx - unrounded| / approx` (approx = the actual reported/ticket-
approximated reward, i.e. "how far off is the number I'm showing you").
Aggregated per rule across ALL of its bound segments in one evaluation,
per C.6's "per-rule materiality check" wording -- not per segment, per
category, or per month.

---

## 2026-08-11 -- Full Stage 5 (caps.py), Part C SS C.2.3 / SS C.2.4

### 9. Spend-measure caps (syn_slab) stay deferred -- confirmed with Satya

Went into this task planning to build all of C.2.3, then found syn_slab's
three earning rules all carry an *empty* selector (`{}`, matches
everything) at different priorities -- under Stage 3's ordinary winner-
takes-all conflict resolution, only the top-priority rule (slab1) could
ever bind; slab2 and slab3 would never fire. The seed's own comment says
this needs "fill-order binaries," which Part B SS B.5 / Part E frame as an
*optimiser* concern (Phase 4) -- the exact evaluator's equivalent isn't
specified in Part C's Stage 5 description. It's a genuinely different
mechanic (ordered band-filling across a rule group) from a reward ceiling,
even though it reuses the Cap schema object for its band-width bookkeeping.

Confirmed with Satya: today's Stage 5 covers the "classic" cap mechanics
(reward-measure, every window kind, every scope kind, both overflow modes)
that syn_ecom/syn_points/syn_fuel/syn_upi all use. `measure="spend"` still
raises. syn_slab's fill-order mechanic is its own future task, and will
likely also need a Stage 3 extension (or a pre-Stage-3 special case) since
its rules can't resolve correctly under today's matching semantics as-is.

### 10. Quarter/anniversary alignment approximated as calendar alignment

C.2.4 documents `{"kind": "quarter", "alignment": "anniversary"}` as
distinct from calendar alignment, and anniversary-year windows generally.
Per A.17 simplification #2 (already adopted engine-wide: "anniversary-year
milestone clocks aligned to the modelling year"), Stage 5 resolves BOTH
anniversary-aligned quarters and anniversary_year caps to the same month
buckets as their calendar-aligned counterparts, and stamps the result with
an `anniversary_approximated` flag (parallel to `cycle_approximated` for
statement_cycle) so the approximation is visible in the trace rather than
silent. True anniversary alignment (using the user's actual card
anniversary month, wallet mode) needs `card_anniversary_month` threaded
through from the user profile -- not built anywhere in the pipeline yet,
deferred until wallet mode lands.

### 11. Multi-category pooled cap windows raise rather than guess

A `rule_group`- or `card`-scoped cap could in principle pool segments
across several different categories/channels within one window instance
(e.g. two different rules in the same group, each targeting a different
category). None of the four in-scope cards' caps do this in practice
(syn_points' cap_portal is rule_group-scoped but only one rule -- portal_
bonus -- is actually tagged into that group). `apply_caps` raises rather
than picking an attribution scheme (proportional? chronological across
categories? per-category sub-caps?) with no real fixture to validate
against. `test_multi_category_pooled_window_raises` covers this.

### 12. Overflow-spend back-derivation inherits category mode's approximation

For `overflow: "base_rate"`, the excess spend past the cap is back-derived
as `excess_reward / flat_rate` (A.3's continuous rate `a`, not Stage 4's
ticket-approximated `ea`). When `ea == a` exactly (true for every in-scope
card's cap-bearing rule at the ticket sizes exercised so far -- percentage
rules with clean paisa amounts), this is exact. If a future capped rule's
`ea` diverges from `a` enough to trip the C.6 materiality flag, the back-
derived excess spend would be a slightly-off estimate layered on top of an
already-estimated reward -- a real but currently theoretical interaction,
since no in-scope fixture's capped rule is ever flagged `rounding_estimated`.
Worth a second look if a future card's cap sits on a materially-imprecise
ticket-approximated rule.

---

## 2026-08-11 -- Stage 6-7 (thresholds.py), Part C SS C.3 / SS C.4

### 13. `activate_rule` payloads deferred -- confirmed with Satya

C.3's payload table lists `activate_rule { rule_id, application: prospective
| retroactive }` for accelerated-rate thresholds (syn_retro, syn_renewal).
Firing it correctly means Stage 3 gaining a concept it doesn't have at all
today: a rule that only becomes matchable once a threshold crosses (Stage
3's `EarningRule` has no `requires_activation` field, and matching is
purely per-segment/stateless -- it has no notion of "which month" or "has
this card crossed X yet"). Prospective activation (syn_renewal's
dining_2x: active only from the crossing month onward) and retroactive
activation (syn_retro: the whole window re-rates once crossed, per
highest_only tier resolution) are different re-application mechanics on
top of that, both needing thresholds.py to reach back into Stage 3/4/5 for
the affected months.

Confirmed with Satya: today's Stage 6-7 covers only the five "grant" type
payloads (grant_points, grant_cashback, grant_voucher, waive_fee,
grant_entitlement) -- self-contained, no dependency on Stage 3/4/5 at all.
`activate_rule` raises **only when a tier carrying it is actually crossed**
(`_require_supported_payload` is checked per firing tier, not upfront for
the whole threshold) -- an uncrossed activate_rule tier is inert and
doesn't block evaluation of tiers that do fire. This is why
`test_syn_renewal_uncrossed_activate_rule_tier_does_not_block_evaluation`
passes while `test_syn_renewal_activate_rule_tier_raises_when_crossed`
(cumulative mode: crossing the 5L grant_points tier necessarily also
crosses the 1L activate_rule tier) correctly raises.

Building activate_rule properly is its own future task and will likely
touch match.py (adding `requires_activation`) as well as thresholds.py.

### 14. `condition: "on_renewal"` carried through unfiltered -- year-mode gap

C.3's payload table ties renewal-benefit milestones to `condition:
"on_renewal"` (should only fire in renewal years, not Year 1). Nothing in
the pipeline built so far has a year_index concept (that's C.4.2 / Stage
11, not built) so thresholds.py can't filter on it -- it just carries
`condition` through on the `Payload` unchanged
(`test_on_renewal_condition_carried_through_unfiltered`). Whichever stage
eventually assembles Year-1 vs steady-state values (Stage 11 per C.4) is
the right place to apply this filter; thresholds.py stays year-agnostic.

**RESOLVED 2026-08-12** -- see entries #59-60 below. `golden_syn_renewal_
year_divergence.json` now exercises the year-mode filter end-to-end.

### 15. `caps.py`'s window helpers made public for reuse

`window_instances`/`window_flags` (formerly `_window_instances`/
`_window_flags`) are now unprefixed -- thresholds.py imports them directly
rather than re-implementing C.2.4 window resolution a third time. Both
Stage 5 and Stage 6-7 need identical quarter/year/anniversary/statement-
cycle resolution, so a later-numbered stage importing an earlier one's
shared vocabulary type is the same direction as thresholds.py importing
`Selector` from match.py, or caps.py importing from match.py before it.
Verified `test_caps.py`'s 18 tests still pass unchanged after the rename.

---

## 2026-08-11 -- Stage 8 (valuation.py), Part A SS A.7 / Part C SS C.2.9

### 16. Transfer routes: ratio computed, not read from the stored field

A.7's formula parenthetical is explicit -- "(for transfers: transfer_ratio
. estimated_partner_point_value, both stored per SS44)" -- so for
`route_type="transfer"`, the effective rupees-per-point is `transfer_ratio
* partner_point_value`, computed fresh, never the route's own stored
`ratio` field. The seed data's transfer route happens to also carry a
`ratio: 1.0` that equals `transfer_ratio(1.0) * partner_point_value(1.0)`
by construction, which could read as "just use ratio directly" -- but the
spec names the computed form as authoritative, and treating the stored
`ratio` as a redundant/legacy field (present for schema uniformity across
route types, e.g. so every route row has *a* ratio-shaped column) is the
more defensible reading if the two ever diverge. `_validate_route`
requires `transfer_ratio`/`partner_point_value` on transfer routes
regardless of whether `ratio` is set.

### 17. Friction defaults to 1.0 flat, not type-dependent, in code

A.7 suggests type-specific friction defaults (cash/voucher 1.0, portal
0.9, transfer 0.75-0.85) but the seed data already encodes exactly that:
portal and transfer set `friction_default` explicitly (0.9, 0.8), stmt/
voucher omit it. So `valuation.py` just defaults any missing `friction` to
a flat 1.0 -- the type-specific defaults live once, in the registry data,
not duplicated as route-type branching in code. If a future currency's
travel_portal route omits `friction_default`, it will get 1.0 (not A.7's
suggested 0.9) until the registry sets it explicitly -- a real gap, but
the alternative (hardcoding "portal -> 0.9" in code) would fight the
registry-is-the-source-of-truth principle C.7 sets up.

### 18. `min_points` unmet -> price at Rs0, not the best alternative route

When the user's declared v_exp route can't be reached (points below
`min_points`), rather than silently substituting a different (reachable)
route's rate, `value_currency` prices at Rs0 and flags
`min_points_not_met`. Silently substituting would mean showing a number
the user never actually declared wanting, which cuts against A.7's "the
engine prices rewards at v_exp everywhere" / "never silently price at
v_opt" instruction -- if the user's chosen route isn't reachable this
year, Rs0-with-a-reason is more honest than an unrequested rate swap.
v_cons/v_opt are unaffected by this (they already exclude ineligible
routes from their own max() search per SS8's "over {cash,voucher} routes" /
"over all routes" wording).

### 19. Flat per-currency redemption fees (A.12's `RedemptionFees(c)`) not modelled

A.12's NACV formula has a `RedemptionFees(c)` line distinct from the
per-point fee this stage implements (`per_point_fee`, scales with points
redeemed). No route in the seed catalog carries a flat fee field, so
there's nothing to test against; deferred until a card needs it. Likely
lands in Stage 10 (COSTS) rather than here, since A.12 groups it with fees/
surcharges/forex, not with the currency pipeline of A.7.

---

## 2026-08-11 -- Stage 9 (benefits.py), Part A SS A.8 / SS A.9, Part C SS C.2.8

### 20. Portfolio dedup computes the value ceiling, not a per-card allocation

A.9's formula is `PortfolioBenefit(b) = Sum_c l(c,b) . V(b)` subject to
`Sum_c l(c,b) <= Need(b)` and `l(c,b) <= Entitle(c,b)` -- on its face an
allocation problem (which card's quota supplies which unit). But every
unit is worth the same `V(b)` regardless of which card provides it, so the
value-maximising total is always exactly `min(Need(b), Sum_c Entitle(c,b))`
-- the specific per-card split doesn't change the value at all. A.9's own
text confirms the split is an *optimiser* decision ("allocated to
whichever card's quota the optimiser draws down... allocation matters when
quotas have qualification gates" -- i.e. it matters for figuring out which
spend to route where, a Phase 4 concern, not for the value number itself).
`deduplicate_portfolio_benefit` therefore returns the achievable value
ceiling, not a card-by-card breakdown -- correct today, and it's the exact
quantity the optimiser needs as its target once it exists.

### 21. Gated countable entitlement sums Stage 6-7's ThresholdEvents; ungated
multiplies by window-instance count

Two different ways `Entitle(c,b)` gets computed, both hand-tested:
qualification-gated benefits (syn_lounge's dom_lounge) sum `quantity` from
every matching `grant_entitlement` ThresholdEvent Stage 6-7 actually
produced (so unqualified quarters contribute nothing); ungated benefits
(no card in the catalog has one -- hand-built test) get a flat
`entitlement * window_instances(entitlement_window)` (e.g. 2/month * 12 =
24/year), reusing caps.py's window machinery for the third time now.

### 22. Voucher/flat_perk kinds: utilisation and friction are caller-supplied,
same posture as Need/V(b)

`utilisation_ref`/`friction_ref` on a Benefit are registry/user pointers
(C.2.8), not values this stage resolves -- `value_voucher_benefit` and
`value_flat_perk_benefit` take `utilisation`/`friction` as plain
parameters, same as Stage 1's `ticket_size` and Stage 8's route friction.
No default is suggested anywhere in the spec for benefit-voucher friction
specifically (A.7's cash/voucher/portal/transfer defaults are for
*redemption routes*, a different mechanism from a milestone voucher grant,
even though A.5 borrows the same phi notation) -- test values (0.85, 0.9)
are illustrative only, not asserted as "the" default anywhere.

### 23. `flat_perk` implemented and tested without a real fixture

No card in the 12-card synthetic catalog uses `kind: "flat_perk"` (only
syn_miles' vouchers and syn_lounge's one countable benefit exist). Built
and tested per A.8's formula (`FV(c,b) . u(c,b)`) anyway since it's a
three-line function directly off the spec text, consistent with how
selector_override (Stage 6-7) and per_point_fee (Stage 8) were handled --
implement what's cheaply spec-complete, flag what's genuinely a scope
decision (activate_rule, spend-measure caps) for sign-off instead.

---

## 2026-08-11 -- Stage 10 (costs.py), Part A SS A.6 / SS A.10 / SS A.11

### 24. GST_RATE=0.18 is a fixed constant, not a per-card parameter

C.2.10's CardRuleSet example shows a per-card `fees.gst_rate` field, but
no card in seeds/synthetic_cards.py sets one (seed.py's INSERT into
card_versions doesn't even have a gst_rate column), and A.6's formulas are
written against a literal `g = 0.18`. Implemented as a fixed module
constant. This also matches golden_syn_ecom_basic.json's own hand
computation exactly ("joining fee 500 * 1.18 = 590"), which is now fully
verified end-to-end (see #26).

### 25. International spend is a direct parameter, not derived from segments

A.10's `ForexCost(c) = m(c) . (1+g) . Sum_t x(c,intl,t)` needs to know
which spend is international. Stage 1's category-mode grid has no
geography dimension (same gap already logged for Stage 3's syn_travel
"intl" earning rule, DECISIONS.md #4) -- `forex_cost()` takes
`international_spend` as a plain caller-supplied Decimal rather than
trying to derive it from SpendSegments, so Stage 10 doesn't need to solve
that gap as a prerequisite. Tested against syn_travel's real
forex_markup=0.0 (proving the zero-forex case is Rs0 regardless of
amount, by construction) plus a hand-built non-zero-markup case.

**UPDATE 2026-08-12**: the geography gap that motivated this is now
closed (#4, #51-54). `forex_cost()` itself is unchanged (still a pure
formula over a plain Decimal, deliberately -- see #52), but a new
`international_spend_total(segments)` now derives that Decimal from
segments via `SpendSegment.geography`, so callers no longer have to
compute it by hand. `golden_syn_travel_forex.json` uses this end-to-end.

### 26. Surcharge waivers are NOT modelled in costs.py -- confirmed by C.9's
own framing, and now verified end-to-end via the golden

C.9 Example 10 (syn_fuel) is explicit: "surcharge waivers are just capped
negative-cost rules" -- the refund is an ordinary earning rule
(fuel_refund, capped at Rs250/month) that flows through Stages 3-5 like
any other reward. `surcharge_cost()` therefore charges syn_fuel's flat 1%
unconditionally; it never needs to know a waiver mechanism exists. No
special-casing was added here, which is itself the evidence the reading is
right -- the card's own schema already routes the offset through the
reward pipeline instead of needing cost-side logic.

Also: golden_syn_ecom_basic.json's `fee_paid`/`waiver_achieved`/
`nacv_steady_state`/`nacv_year_1` fields -- unverifiable until this stage
existed -- are now asserted exactly (extended `_load_card_rules`'s sibling
`_load_thresholds` to parse the card's real waiver threshold, run Stage
6-7, then Stage 10's `compute_fees`). syn_ecom has no benefits/surcharges/
forex, so NACV = gross_reward - fees exactly for this card; a future
golden with a surcharge or benefit will need those terms added at the
test level (costs.py/benefits.py both already support them, this is
purely about which golden exercises which terms).

---

## 2026-08-11 -- Stage 11 (assemble.py), Part A SS A.12, Part C SS C.4.2 / SS C.10

### 27. The explanation trace is a flat line-item list, not C.10's full node
schema -- a real scope reduction, flagged here rather than asked upfront

C.10 specifies a rich per-node schema: `amount`, `kind`, `card_version`,
`rule_id`, `cap_state`, `window`, `spend_basis`, a `currency` sub-object
(`points`/`route`/`v`/`phi`), `flags`, `source_refs`. Building that fully
would mean plumbing far more intermediate state through every earlier
stage than is currently exposed -- e.g. Stage 5's per-window cap_state
(bound/unbound) isn't returned anywhere today, Stage 8's per-currency v/phi
detail is internal to `value_currency` and not surfaced per reward line,
and `source_refs` comes from Part D's `source_links` table, which doesn't
exist at this in-memory engine layer at all (it's a Postgres concern).

Implemented instead: `TraceLine(kind, amount, label, flags)` -- enough to
reconstruct the total by summing and to show which named component
contributed what, at the granularity Stage 11 actually has (gross reward,
each milestone grant, benefit value, fee, forex, surcharge). This is a
real reduction in fidelity versus C.10's spec, not just an implementation
detail -- unlike other decisions in this log (which were "the spec doesn't
pin down X, here's a defensible reading"), this is "the full spec'd thing
is meaningfully larger than what's cheap to build today." Flagging
explicitly: if the richer trace (cap_state, per-currency breakdown,
source_refs) is needed soon -- e.g. for the SS37 "Why Card B" panel or
SS74's audit requirement, both of which C.10 says render this trace
directly -- it's a follow-up task, not a small addition.

### 28. `PV` (per C.4's Stage 11 line) isn't a separate output -- it equals
NACV for a single card

C.4's Stage 11 description lists "NACV per card, PV, Year-1 and Steady-
State variants..." -- but Part B's `PV(P,x) = Sum NACV(c|x) - lambda .
max(|P|-1,0)` is a *portfolio* (multi-card) concept, and for a single-card
portfolio the complexity penalty term is `max(1-1,0)=0`, so `PV({c},x) =
NACV(c)` exactly, trivially. True portfolio PV needs the enumeration/
allocation machinery of Part E (the optimiser, Phase 4), which doesn't
exist yet. No separate `PV` output was added -- `NACVResult.steady_state`
already is that number for the single-card case this engine currently
evaluates.

### 29. WelcomeValue and RedemptionFees stay deferred, now formally in the formula

`assemble_nacv` accepts `welcome_value` as an optional parameter (default
Rs0) so A.12's Year-1 formula is spec-complete even though no card in the
12-card catalog has a welcome-bonus payload type to test non-zero against
(C.3's payload table doesn't define one either -- welcome benefits aren't
modelled anywhere in Part C's schema as read so far). RedemptionFees
stays deferred exactly as already logged at Stage 8 (#19) -- nothing new
here, just confirming Stage 11 doesn't need to solve it either.

---

## 2026-08-11 -- breakpoints.py, Part C SS C.0 / Part E SS E.0

### 30. Every breakpoint expressed in spend-domain rupees, caps converted via A.3

C.0's repair-pass pseudocode operates entirely on "exact milestone/waiver-
eligible spend" and "top-up ... spend to cross T(beta)" -- spend-domain
throughout. Threshold tiers are already spend-domain by construction
(`basis.measure` is milestone/waiver_eligible_spend, `threshold_amount`
literally is the rupee value), but a reward-measure cap's `amount` is a
REWARD ceiling, not a spend value. Converted via A.3's `Sbar = Cap/a`
using the capped rule's own continuous rate -- the exact same formula
caps.py's own overflow-spend derivation already relies on, so `flat_rate`
was made public (was `_flat_rate`) rather than reimplemented a second
time. This keeps every entry in the compiled list comparable on one axis,
which the (future) repair pass needs to do its "near-miss just below /
barely-made" comparisons meaningfully across mixed threshold/cap sources.

### 31. Only the compile step is built -- the repair pass is a separate,
later module by design, not an oversight

Part E SS E.0's module layout lists `breakpoints.py` under `engine/`
(compiled breakpoint list) and `repair.py` under `optimiser/` (the actual
near-miss/barely-made variant generation and repair pass) as two
DIFFERENT files in two different directories -- confirming this is meant
to be a clean split, not an artificial one. `default_buffer` (C.0's `max
(Rs5,000, 2% . T(beta))`) is computed and stored on every breakpoint here,
since the repair pass will need it immediately once it exists, but the
walk-the-list-and-generate-variants algorithm itself is out of scope for
this module -- that's Phase 4's optimiser, not Phase 2's engine.

### 32. Spend-measure caps raise here too, same posture as caps.py

`compile_card_breakpoints` raises on any cap with `measure != "reward"`,
consistent with caps.py's own scope boundary (docs/DECISIONS.md #9) --
syn_slab's spend-measure band caps aren't ordinary reward ceilings and
don't have a defined Sbar conversion the way a reward-measure cap does
(there's no "reward ceiling" to convert from; the amount already is a
spend boundary, but interpreting it as a breakpoint requires resolving
syn_slab's fill-order mechanic first, which stays deferred).

---

## 2026-08-12 -- Second and third goldens: syn_miles, syn_lounge

### 33. Bug found and fixed: golden adapter never propagated a rule's currency

Both new goldens failed on the very first run with `KeyError: None` inside
`value_accrual_results`. Cause: `seeds/synthetic_cards.py`'s raw
`earning_rules[i]["accrual"]` dicts always carry `"currency": None` --
that's a placeholder. `seed.py` itself stamps the real value in before
inserting (`accrual["currency"] = card["currency"]`, seed.py line ~101),
which the golden adapter's `_accrual_from_dict` never mirrored -- it just
read the placeholder `None` straight through. Invisible for
golden_syn_ecom_basic.json because cashback_inr's trivial v===1 pricing
never actually needs to look the currency up by key anywhere in that
golden's assertions. Fixed by threading `card["currency"]` into
`_accrual_from_dict` from `_load_card_rules`, matching seed.py's own
behaviour exactly. A good example of why "wire a second real golden" finds
things a single golden can't -- this bug was latent in the adapter since
Stage 4's golden, just never exercised.

### 34. Per-category custom seasonality added to the golden JSON format

golden_syn_lounge_quarterly_gate.json needed two qualifying quarters and
two that fall short (per-quarter independence, C.9 Example 11's whole
point) -- not expressible as a single annual total under uniform
seasonality. Extended `_parse_spend_annual` to accept an optional
`seasonality` dict (category -> 12-weight list), reusing `normalise()`'s
existing non-uniform seasonality support (already built and tested at
Stage 1) rather than adding new machinery. Weights were chosen so both
the monthly amounts AND the resulting points (ea=1/75 exactly, zero floor
loss at the chosen Rs600 dining ticket) land on clean, hand-verifiable
numbers -- deliberate, not incidental: any weight split that didn't
divide cleanly would still be computed correctly (Stage 1's paisa-exact
reconciliation guarantees that), but would be much harder to state a
concise, checkable `_hand_computation` against, per CLAUDE.md's testing
rule.

### 35. `assumptions` block now carries structured values, not just prose

golden_syn_ecom_basic.json's `assumptions.note` was free text. The two new
goldens need actual machine-readable values with no home anywhere in the
card schema -- `primary_route` (per currency), `benefit_need`/
`benefit_unit_value` (per benefit), `voucher_utilisation`/
`voucher_friction` -- since these are user/registry inputs (C.7), not card
facts. Added as structured sibling keys alongside the existing free-text
`note`, read directly by the test rather than defaulted anywhere -- same
"caller supplies, engine never invents" posture as Stage 8/9's own
utilisation/friction parameters.

---

## 2026-08-12 -- activate_rule payloads (Part C SS C.3), superseding #13

### 36. `requires_activation` lives on match.py's EarningRule, gated at match time

The gap flagged in #13 -- Stage 3 had no concept of a rule that only
matches once a threshold fires -- is now closed. `EarningRule` gained
`requires_activation: bool = False`; `match_segment`/`match` gained an
`active_rule_keys: frozenset[str] = frozenset()` parameter and now filter
`eligible = [r for r in earning_rules if not r.requires_activation or r.key
in active_rule_keys]` before matching. Default empty set means passing a
card's FULL rule list (activation-gated rules included) to an ordinary
Stage 3 call is always safe -- a dormant rule can never accidentally win a
segment. Backward compatible: all 139 pre-existing tests passed unchanged
after this change, since no existing fixture ever set
`requires_activation=True`. `thresholds.py` is the only caller that ever
passes a non-empty `active_rule_keys`.

### 37. Prospective activation excludes the crossing month itself

C.4's "rate unlocks apply from crossing month onward" doesn't say whether
"onward" includes the crossing month. Implemented as EXCLUSIVE (active
months = strictly after the crossing month) for two reasons: (1) the
engine has no intra-month transaction timing, so by the time a month's
cumulative total is known to have crossed the threshold, that month's
spend has already been fully accrued at the old rate -- backdating into it
would need information the engine doesn't have; (2) it matches how real
card programmes typically behave (a tier unlock takes effect from the next
billing cycle, not retroactively mid-cycle). Consequence, tested directly
(`test_prospective_activation_crossing_in_final_month_activates_nothing`):
if the crossing happens in the window's last month, the rule never
actually activates that year at all -- an edge case worth knowing about,
not a bug.

### 38. `evaluate_threshold` now computes `crossing_month` for every firing
tier, not just activate_rule ones

A chronological running-total walk over each window instance's months,
recording the first month each firing tier's threshold was met. Needed for
prospective activation timing, but computed universally since it's cheap
(one pass per window instance) and generally useful for trace/
explainability regardless of payload type (e.g. "this voucher was earned
in July") -- consistent with the "flag/store it since a later stage will
want it" posture already used for cap buffers in breakpoints.py.

### 39. `apply_rule_activations`: segment-identity-based splice, not a full
re-pipeline run

For each activate_rule event, determines the active months (retroactive:
the whole window; prospective: strictly after crossing_month per #37),
finds the reward segments in those months matching the activated rule's
selector, drops whatever ORIGINALLY bound to each such segment (matched by
Python object identity -- `is`, not `==` -- since Stage 1/2/3's segment
references flow through unchanged for any uncapped binding), re-runs
Stage 3's match_segment with the rule now eligible, and re-runs Stage 4 on
the new binding. This is a targeted patch over Stage 4/5's output, not a
full pipeline re-run, which is both cheaper and correctly leaves
unaffected segments (different months, different categories) untouched.

Two known gaps from relying on identity, both currently unreached because
no in-scope card combines the two mechanics: (a) caps.py's synthetic
overflow segments are NEW objects (`dataclasses.replace`), so an
activation affecting an already-capped rule wouldn't be reconciled
correctly; (b) per the official C.4 stage order (Stage 5 CAP precedes
Stage 6-7 THRESHOLDS), `apply_rule_activations` runs AFTER `apply_caps` in
the intended pipeline, meaning a cap on the newly-activated rule itself
would need a second capping pass that doesn't exist. Neither syn_retro's
rate_2/rate_3 nor syn_renewal's dining_2x carry a cap, so this is
documented as a boundary, not fixed speculatively.

### 40. Golden adapter updated to carry `requires_activation` through

`test_goldens.py`'s `_load_card_rules` now reads `er.get(
"requires_activation", False)` into `EarningRule`, matching seed.py's own
schema field, so a future golden for syn_retro or syn_renewal (exercising
activate_rule end-to-end through the JSON-driven harness, not just direct
engine-level tests) won't need this fixed retroactively. No existing
golden currently needs it (syn_ecom/syn_miles/syn_lounge have no
activation-gated rules).

---

## 2026-08-12 -- Spend-measure caps + syn_slab (Part A SS A.3, C.9 Example 7),
superseding DECISIONS #9's deferral

### 41. `tier_mode="incremental"` added to match.py, unconditionally excluded
(no activation opt-in, unlike requires_activation)

syn_slab's three rules (slab1/2/3) all carry an identical empty selector
`{}` at different priorities -- under ordinary winner-takes-all resolution
only the highest-priority one (slab1) could ever bind, exactly the problem
flagged back at Stage 5/6-7. `EarningRule` gained `tier_mode: str | None =
None`; `match_segment`'s eligibility filter now also excludes `tier_mode
== "incremental"` rules, with NO opt-in mechanism (unlike
`requires_activation`'s `active_rule_keys`) -- there's no scenario where an
incremental-band rule should ever win a segment on its own via ordinary
matching; it only ever binds through `apply_incremental_bands`. Backward
compatible: all 147 pre-existing tests passed unchanged (default `None`
never matches `"incremental"`).

### 42. Incremental bands are a genuinely separate mechanic from ordinary
caps, not a variant -- confirmed by how little they share in code

`caps.py`'s new `apply_incremental_bands` pools ALL matching segments
across a window into one total, then walks the group's rules in
DESCENDING PRIORITY order (syn_slab: slab1 priority 30 > slab2 20 > slab3
10, exactly matching band1/band2/uncapped's intended fill order), giving
each band `min(remaining, cap.amount)` of spend at its own rate before
moving to the next. This shares almost nothing mechanically with
`apply_caps`'s reward-ceiling-and-overflow logic (different measure,
different pooling direction, different rounding treatment) beyond reusing
the same `Cap`/`Window` types and `window_instances` -- confirming the
original call to treat it as a separate function rather than extending
`apply_caps` was right. `_validate_incremental_cap` requires
`measure="spend"` and `scope="rule"` specifically (syn_slab's only
fixture), raising on anything else rather than guessing at semantics for
`rule_group`/`card`-scoped or reward-measure incremental caps that no
current card needs.

### 43. Band rules must share an identical selector -- enforced, not assumed

All three slab rules share selector `{}` in the fixture, so this wasn't
forced by necessity, but the model only makes sense if every band rule
targets the SAME underlying spend pool (they're rate slices of one total,
not independently-targeted rules) -- `apply_incremental_bands` raises if
any band rule's selector differs from the first one's, rather than
silently pooling mismatched selectors together.

### 44. Reward per band uses `accrue_transaction` directly on the band's
spend, not `accrue_category_mode`'s ticket-size approximation

Each band's spend is already an aggregate annual (or per-window) total by
construction -- there's no "ticket size" to approximate through, so
`accrue_transaction(accrual, band_spend)` (treating the whole band as one
transaction) is exact, not an estimate. This is mathematically identical
to `floor_on_aggregate` regardless of the rule's own `rounding` string
(both floor a single aggregate amount to the paisa) -- syn_slab's rules
happen to say `floor_paise_per_txn`, and it doesn't matter which string is
there for this purpose. No `rounding_estimated` flag is ever produced by
this path, correctly -- there is no approximation happening.

### 45. Synthetic segments use `category="incremental_band"`; golden-adapter
wiring deferred

A band's spend is pooled across every matching real category (selector
`{}` matches everything for syn_slab), so there's no single real category
to attribute a band's `AccrualResult.segment` to -- a clearly-marked
synthetic category is used instead (parallel to caps.py's own synthetic
overflow segments), tagged with the window's last month. Separately: the
raw seed schema has NO `tier_mode` field at all (not even a `seed.py`
INSERT column) -- syn_slab's rules are only identifiable as incremental by
their rule_group sharing spend-measure caps.

**Update 2026-08-12**: the golden-adapter gap is now closed --
`_load_card_rules` infers `tier_mode="incremental"` for any rule whose
`rule_group` contains a spend-measure cap anywhere in it (catches slab3
too, even though it's itself uncapped, because it shares `rule_group=
"slab"` with slab1/slab2). `golden_syn_slab_bands.json` wires this
end-to-end: Rs5,00,000 pooled annual spend -> band1 Rs1,000 + band2
Rs4,000 + band3 Rs6,000 = Rs11,000 gross, Rs0 fee (this card has none),
NACV steady-state = Year-1 = Rs11,000, 3yr = Rs33,000. Passed on the first
run against the same hand computation as `test_incremental_bands.py`'s
direct-call tests -- good confirmation the two paths (direct engine call
vs. full JSON-driven pipeline) agree.

---

## 2026-08-12 -- Wire golden_syn_upi_channel.json (C.9 Example 9)

### 46. First golden to exercise Stage 2 exclusions -- adapter gap closed

All four prior goldens called `apply_eligibility(normalised,
exclusions=())` -- none had a real exclusion in their card's fixture.
`test_goldens.py` had no `_load_exclusions`/`_exclusion_selector_from_dict`
at all until now; added, mirroring `_selector_from_dict`'s pattern
(categories/channels/merchant_groups only, matching what eligibility.py's
own `_selector_matches` supports). syn_upi's single exclusion
(`upi_fuel_rent`: channels=[upi], categories=[fuel,rent], excluded_from=
[rewards]) is the fixture.

### 47. This card has no base/catch-all earning rule -- asserted explicitly,
not just implied by the totals

syn_upi's only earning rule requires `channels=[upi]`; there's nothing
else. The golden spend deliberately includes Rs10,000/month of ordinary
(non-UPI-channel) grocery spend specifically to prove it earns nothing --
`bound_categories == {"grocery"}` with `all(... channel == "upi")` checks
that Stage 3 produced zero bindings for the non-UPI segments, not just
that the final reward total happens to match (a total-only check could
pass even if bindings were wrong in an offsetting way).

### 48. Cap deliberately sized to bind, not just be present

Initial spend sizing left the Rs500/month cap unexercised (any grocery
spend under Rs50,000/month never reaches it) -- untested-but-present
caps in a golden defeat the point of C.9 Example 9 being partly about the
cap. Resized UPI grocery to Rs60,000/month (600pts uncapped) specifically
so the cap trims it to 500pts/month with the 100pts/month excess
genuinely discarded (overflow="zero", no fallback rate to re-rate it to,
unlike syn_ecom's base_rate overflow).

---

## 2026-08-12 -- Wire golden_syn_fuel_surcharge.json (C.9 Example 10)

### 49. First golden to exercise Stage 10 surcharges -- adapter gap closed

Same pattern as #46 (exclusions): no prior golden's card had a surcharge,
so `test_goldens.py` had no `_load_surcharges` at all. Added, reusing
`_selector_from_dict` directly (Surcharge's selector is the same C.2.1
type as an earning rule's, no separate `_surcharge_selector_from_dict`
needed the way exclusions got their own -- eligibility.py's
`ExclusionSelector` and match.py's `Selector` are separate dataclasses,
but costs.py's `Surcharge.selector` is typed as match.py's `Selector`
directly).

### 50. Chosen to stack three previously-separate mechanics deliberately,
not to keep the golden "clean"

syn_fuel combines `stacks_with_base` (fuel_refund adds onto base rather
than replacing it -- first golden to exercise stacking), a reward cap that
actually binds and trims the stacked rule specifically (not the base rule
it's stacked onto), a surcharge that costs MORE than the reward it's
levied against generates on its own (Rs4,248 surcharge vs the fuel
portion's Rs1,800+3,000=Rs4,800 combined reward -- a thin but positive
margin once grocery's Rs300 is added), and a waiver threshold. The
resulting NACV (Rs852 steady-state) is a realistic illustration of A.11's
point that a fuel card's surcharge can eat most of its own reward value --
not smoothed into a "nicer" number for the golden's sake.

---

## 2026-08-12 -- Geography-aware selectors (Part C SS C.2.1, Part A SS A.10),
resolving #4, wire golden_syn_travel_forex.json

Confirmed with Satya: close the geography deferral properly rather than
work around it again. Full feature, not a golden-specific patch.

### 51. `SpendSegment.geography` defaults to "domestic", not `None`

Unlike `channel` (`None` = genuinely unspecified, resolved later by a
category->channel mapping) geography isn't optional in reality -- every
real transaction happened domestically or internationally, no
"unspecified" state exists. `CategorySpend.geography: str = "domestic"`
threads straight onto each `SpendSegment` it produces; UPI-decomposed
segments stay domestic unconditionally (UPI has no international rails,
never needed a parameter). `normalise()` validates against
`VALID_GEOGRAPHY = {"domestic", "international"}`, rejecting anything else
outright rather than defaulting silently.

### 52. `selector_matches` consolidated into match.py, reused by three
other modules; eligibility.py kept separate (different type)

Adding geography meant touching every module with its own selector-
matching copy. Rather than editing four near-identical functions in
parallel (and risking drift), made match.py's `_selector_matches` public
(`selector_matches`) and had caps.py/thresholds.py/costs.py import it
directly, deleting their own copies -- all three already operated on
match.py's `Selector` type, so this was a straight substitution, not a
type-unification exercise. eligibility.py's `ExclusionSelector` predates
match.py and is a genuinely different dataclass, so it keeps its own
parallel implementation (now also updated for geography + merchant_group,
see #53) rather than forcing a type merge that wasn't asked for.

`geography="all"` matches every segment regardless of its own geography
(C.2.1's explicit third value, distinct from leaving the field `None`,
though both are unrestricted in effect) -- tested directly
(`test_geography_all_matches_every_segment`).

`costs.py::forex_cost` keeps its existing plain-Decimal signature (a pure
formula, like `accrue_transaction`) rather than being changed to take
segments directly the way `surcharge_cost` does -- a new
`international_spend_total(segments)` derives the Decimal from segments
separately, composed by the caller. Preserves every existing
`forex_cost()` call site and test unchanged.

### 53. Bug found while wiring the golden: eligibility.py's ExclusionSelector
was also missing merchant_group matching, unrelated to geography

While touching eligibility.py for geography, found `merchant_groups` had
been sitting in `_UNSUPPORTED_SELECTOR_FIELDS` this whole time even though
match.py's `Selector` (and every card's actual exclusions) treat it as a
normal supported field -- no current exclusion fixture uses
`merchant_groups`, so nothing ever exercised the gap. Fixed alongside
geography since both needed the same `_selector_matches` edit in
eligibility.py; not scope creep, just noticed while already there.

### 54. Bug found and fixed: the GOLDEN ADAPTER's `_selector_from_dict`
never read `geography` from the raw seed dict at all

The engine-level fix (match.py, #51-52) was correct on its own, but
`golden_syn_travel_forex.json` still failed on the first run:
`domestic_rule_keys` came back `{"intl"}` instead of `{"base"}` --
domestic grocery was binding to the international-only rule. Cause:
`test_goldens.py::_selector_from_dict` only ever read `categories`/
`channels`/`merchant_groups` from the raw dict; `geography` was silently
dropped, so the "intl" rule's `Selector` had `geography=None` (no
restriction at all) and matched everything, and being higher-priority
than base, won universally. Fixed by adding the missing `geography` read
(and, for consistency, to `_exclusion_selector_from_dict` too, though no
current exclusion needs it). A good illustration of why the golden is
worth building even when the underlying engine change already has its own
unit tests -- the adapter is a second, independent translation layer with
its own bugs to catch.

`golden_syn_travel_forex.json`: Rs15,000/mo domestic grocery (base, 1pt/
Rs100) + Rs10,000/mo international_flights (intl wins over base, 2pt/
Rs100, REPLACING not stacking) -> 4,200pts/yr -> Rs1,890 via the portal
route. Forex cost Rs0 (this card's own zero markup) vs a hand-computed
Rs4,956 contrast at the seed's default 3.5% markup on the identical spend
(asserted in the test, not part of this card's NACV). Fee waived
(Rs3,00,000 spend past the Rs2,50,000 threshold), but the joining fee
still applies in Year-1 regardless -> NACV steady-state Rs1,890, Year-1
-Rs1,650, 3yr Rs2,130. 168/168 tests total (7th golden).

---

## 2026-08-12 -- `CategorySpend.merchant_group` (Part C SS C.2.1), resolving
#5, wire golden_syn_points_portal_stacking.json (C.9 Example 3)

### 55. `SpendSegment.merchant_group` finally gets a Stage 1 input path --
the deferral in #5 closed exactly the way it predicted

#5 (2026-08-11) added `merchant_group` to `SpendSegment` for Stage 3's
sake but deliberately left `normalise()` unable to populate it, "deferred
until a card actually needs it end-to-end through the full pipeline."
syn_points (C.9 Example 3, portal_bonus's `merchant_groups: [synth_portal]`
selector) is that card. Fix is the same shape as geography's (#51): added
`merchant_group: str | None = None` to `CategorySpend`, threaded straight
onto each `SpendSegment` normalise() produces. Unlike geography, `None` is
a real, permanent state here (not "resolved later") -- most spend has no
merchant-group breakdown at all, the same status `channel` already has --
so no validation, no default-to-a-string the way geography got.

The golden adapter (`test_goldens.py::_parse_spend_annual`) needed a way
for a golden's `spend_annual` dict to express a merchant group per spend
line. Extended the existing "category[/channel][@geography]" key-suffix
convention with a third token: "category[/channel][~merchant_group]
[@geography]" (e.g. "hotels_domestic~synth_portal"). This "~" spelling is
adapter-only shorthand for golden JSON files, not part of C.2.1's own
schema -- real card/user spend declarations don't need it, since C.2.1's
actual input vocabulary has no per-merchant-group spend-entry concept
either (merchant_group is a rule-selector dimension, not a user-spend
dimension, until/unless a real ingestion source supplies it).

### 56. `golden_syn_points_portal_stacking.json` hand computation

Rs1,50,000/mo hotels_domestic tagged merchant_group=synth_portal (matches
BOTH base, 5pt/Rs150, and portal_bonus, 20pt/Rs150, `stacks_with_base`) +
Rs30,000/mo dining (matches only base). Both categories' ticket sizes
(Rs9,000, Rs600) are exact multiples of the Rs150 per-unit divisor, so
floor(ticket/U) loses no remainder anywhere -- zero floor loss, matching
the design discipline already used in syn_upi/syn_fuel's goldens.

Per month: base = 5,000 (hotels) + 1,000 (dining) = 6,000pts, uncapped
(base has no cap of its own). portal_bonus = 20,000pts uncapped, but
`cap_portal` (15,000/mo, `scope: rule_group:portal_accel`, `overflow:
zero`) pools ONLY rules in that rule_group -- base isn't a member, so its
reward on the very same hotels_domestic spend is untouched by this cap,
the point of the golden's `purpose` field. Capped bonus = 15,000/mo.
Annual gross = (6,000+15,000)*12 = 2,52,000 pts.

Valued at the declared primary route "portal" (travel_portal, ratio 0.50,
friction_default 0.9, no per-point fee) -> Rs0.45/pt -> gross reward value
Rs1,13,400.00 -- the first golden to price a non-1.0-friction route (prior
synth_points goldens all used "stmt", ratio-only). Waiver threshold
(Rs3,00,000 cumulative annual, no exclusions) clears easily on Rs21,60,000
total spend -> annual fee waived; joining fee (Rs2,500) still applies in
Year-1 regardless, same as every other waived-fee golden. NACV
steady-state Rs1,13,400.00, Year-1 Rs1,10,450.00, 3yr Rs3,37,250.00.
169/169 tests total (8th golden).

---

## 2026-08-12 -- Wire golden_syn_flat_baseline.json (C.9 Example 1) and
golden_syn_waiver_divergence.json (C.9 Example 5)

### 57. `golden_syn_flat_baseline.json` hand computation

The deliberately plainest golden in the battery: one uncapped percentage
rule (1.5%, `floor_paise_per_txn`), no caps, no thresholds, no exclusions,
no fees at all (`annual_fee`/`joining_fee` both 0). Rs2,40,000/yr grocery +
Rs1,20,000/yr dining, both tickets even so `ticket*1.5%` lands on an exact
paisa amount already -- zero floor loss. Gross reward = 3,60,000*0.015 =
Rs5,400.00. No fee either year -> NACV steady-state = Year-1 = Rs5,400.00,
3yr = Rs16,200.00. Exists mainly as a regression tripwire: if this one ever
goes red, the bug is in the pipeline's plumbing, not in some construct-
specific interaction.

### 58. `golden_syn_waiver_divergence.json` hand computation -- the point is
that "waiver-eligible spend" and "reward spend" are different populations

syn_waiver carries two exclusions pointing in opposite directions:
`rent_no_waiver` (rent earns reward, does NOT count toward the waiver) and
`fuel_no_rewards` (fuel earns nothing, DOES count toward the waiver).
Chose spend specifically so the two views diverge in the direction that
matters: reward view (grocery+rent = Rs4,20,000) and even raw total spend
(Rs5,16,000) both clear the Rs3,00,000 waiver tier comfortably, but the
waiver view (grocery+fuel = Rs2,16,000, since rent is dropped and fuel is
kept) does not -- fee stays charged. A golden that instead crossed the
threshold either way (or used only one exclusion) wouldn't actually catch
a bug that summed the wrong view; this one does, by construction.

Gross reward = (1,20,000+3,00,000)*0.01 = Rs4,200.00 (fuel earns 0, having
been excluded from the reward view at Stage 2 entirely -- it never reaches
Stage 3). Fee not waived: joining_fee=999, annual_fee=999 both charged ->
steady_fee = 999*1.18 = Rs1,178.82, year1_fee = 1,998*1.18 = Rs2,357.64.
NACV steady-state = 4,200.00-1,178.82 = Rs3,021.18. Year-1 = 4,200.00-
2,357.64 = Rs1,842.36. 3yr = 1,842.36+2*3,021.18 = Rs7,884.72.
171/171 tests total (9th and 10th goldens). Only syn_renewal and syn_retro
remain unwired.

---

## 2026-08-12 -- Year-mode split (Part C SS C.3, Part A SS A.12), resolving
#14; wire golden_syn_retro_tiers.json (C.9 Example 6) and
golden_syn_renewal_year_divergence.json (C.9 Example 12) -- 12/12 golden coverage

### 59. `assemble.py` gains `value_milestone_grants_by_year_mode` +
`assemble_nacv(milestone_value_year1=...)` -- additive, not a signature break

#14 flagged Stage 11 as "the right place" for `condition: "on_renewal"`
filtering back on 2026-08-11, without picking an exact mechanism. Landed
on the narrowest addition that keeps every existing call site byte-for-
byte unchanged: `value_milestone_grants_by_year_mode` runs the existing
`value_milestone_grants` twice over the same event list -- once
unfiltered (steady-state total, a renewal year IS a renewal) and once
with `condition == "on_renewal"` events dropped (Year-1 total, nothing
has renewed yet) -- and `assemble_nacv` gets one new optional parameter,
`milestone_value_year1: Decimal | None = None`, defaulting to
`milestone_value` when omitted. All 10 pre-existing goldens and every
other `assemble_nacv`/`value_milestone_grants` call site are unaffected;
only a card that actually has an on_renewal grant needs the new function
and the new parameter at all. Considered instead changing
`value_milestone_grants` itself to take a year-mode flag, but that would
force every caller (all 10 existing goldens) to start passing one for no
behavioural gain -- splitting into a second wrapper function keeps the
original simple and single-purpose, matching how `apply_incremental_bands`
sits alongside `apply_caps` rather than growing a mode flag on it.

Confirmed this stays within C.4.2's own stated MVP simplification (3-year
cumulative treats years 2-3 as identical steady-state re-runs, no spend-
growth modelling) -- the fix only changes which grants VALUE into which
year's MilestoneValue; it does not attempt to re-simulate Stage 3/4's
activation timing per year-mode (e.g. "dining_2x active from month 1 in a
renewal year because last year's spend already crossed the threshold").
That would be a materially bigger feature with its own assumption
questions (does the optimiser's steady-state model assume prior-year
history at all?) and nothing in Part C's C.9 catalogue currently needs it
-- flagging here rather than quietly picking a scope.

### 60. `golden_syn_retro_tiers.json` and `golden_syn_renewal_year_
divergence.json` hand computations

**syn_retro** (C.9 Example 6): flat Rs35,000/mo grocery, ticket 700 (zero
floor loss at 1%/2%/3% alike). Annual 4,20,000 crosses both tiers
(Rs1,00,000 in month 3, Rs3,00,000 in month 9), but `tier_mode:
highest_only` fires ONLY the higher tier's `activate_rule(rate_3,
retroactive)` -- rate_2 never activates at all, and retroactive re-rates
the WHOLE window (all 12 months), not just the months after crossing.
Every month ends at 3%: 35,000*0.03*12 = Rs12,600.00. No caps, no
milestone grants (activate_rule contributes 0, its effect is already
inside the re-rated gross reward), no fee at all (`annual_fee` unset ->
0) -> NACV steady-state = Year-1 = Rs12,600.00, 3yr = Rs37,800.00.

**syn_renewal** (C.9 Example 12) is the golden the whole #14 fix exists
for. Flat Rs45,000/mo dining, ticket 600 (zero floor loss at 1pt or 2pt
per Rs100). Cumulative crosses tier 1 (Rs1,00,000) in month 3 ->
`activate_rule(dining_2x, prospective)` fires, re-rating months 4-12 only
(the crossing month itself keeps the original rate, per #33's prospective
convention) -- months 1-3 at 450pts/mo (1pt/Rs100), months 4-12 at
900pts/mo (2pt/Rs100, REPLACING base, not stacking). Annual = 1,350 +
8,100 = 9,450 pts, valued at the declared "stmt" route (ratio 0.25, no
friction override) -> Rs2,362.50. Cumulative ALSO crosses tier 2
(Rs5,00,000) in month 12 -> `grant_points(10,000, condition: on_renewal)`
fires -- both tiers fire independently since this threshold is
`cumulative`, not `highest_only`. Valued at the same route: 10,000*0.25 =
Rs2,500.00 in the steady-state total, Rs0.00 in Year-1's (the whole point).
No caps, no waiver threshold (fee always charged): joining_fee=1,500,
annual_fee=1,500 -> steady_fee=1,500*1.18=Rs1,770.00, year1_fee=
(1,500+1,500)*1.18=Rs3,540.00.

NACV steady-state = 2,362.50+2,500.00-1,770.00 = Rs3,092.50. NACV Year-1 =
2,362.50+0.00-3,540.00 = **-Rs1,177.50** -- genuinely negative, an
illustrative and intentional result: in year one the accelerated rate
hasn't fully kicked in, there is no renewal bonus yet, and the joining fee
stings on top of the annual fee. 3yr = -1,177.50+2*3,092.50 = Rs5,007.50.
177/177 tests total (11th and 12th goldens) -- full 12/12 C.9 golden
coverage reached.

---

## 2026-08-12 -- Phase 3: `engine/evaluate.py` orchestrator, `POST /evaluate`,
`POST /next-best-spend` (Part E SS E.0/E.1/E.5/E.12)

Two scope decisions confirmed with Satya before writing any endpoint code
(both via plan-mode discussion, not silently picked):

### 61. `/next-best-spend` scope: annual marginal-delta MVP, not wallet
mid-year state

E.5/E.12 describe Next-Best-Spend seeded by wallet mode's
`current_year_progress` (mid-year state: what's already happened this
year, per-threshold spend to date). That machinery doesn't exist anywhere
in the engine -- wallet mode itself isn't built (extends #10's existing
anniversary-alignment gap: both need a "where are we in this card's year"
concept that doesn't exist yet). Building it now would mean designing how
to seed already-triggered threshold/cap state and evaluate only the
remaining months of the year -- a real new engine feature, not an endpoint
wiring task, and nothing in C.9's catalogue currently needs it.

Confirmed scope instead: given a full annual spend profile (the same
shape `/evaluate` already takes), compute the exact incremental steady-
state NACV of routing +Δ (default ₹1k/10k/50k) to a category on a
candidate card -- two `evaluate_card` calls (baseline, baseline+Δ), zero
new engine capability. This is still a genuine, useful slice of E.12's
"marginal bands" idea (§39's kink-annotated marginal value curve), just
without the mid-year seeding. True wallet state stays open (folded into
#10's table row, not a new orphaned topic) until a wallet-mode pass
actually needs it end-to-end -- same "additive when needed" posture as
every prior deferral resolution in this log.

### 62. `PostgresCardRepository` deliberately not built this pass

`compute/.env`'s `DATABASE_URL` points at a Supabase project host
(`db.<ref>.supabase.co`) that fails DNS resolution from the dev sandbox --
general internet DNS resolves fine (`google.com`, `supabase.com`), so it's
specific to that host, most likely Supabase's direct-connection endpoint
requiring IPv6 that this sandbox's resolver can't reach (the fix is
probably swapping to Supabase's IPv4 connection-pooler host). No local
Postgres or Docker is available in the sandbox either, so there is no way
to write and verify DB-reading code here -- confirmed with Satya: fix
connectivity before that path is built, rather than ship queries that
were never actually run (CLAUDE.md: "never claim something works without
having run it").

`app/repository.py`'s `CardRepository` protocol is intentionally the seam
this decision implies: `SyntheticCatalogRepository` (backed by
`seeds/synthetic_cards.py`, the same fixtures the golden battery uses) is
the only implementation today; a `PostgresCardRepository` reading
`card_versions`/`earning_rules`/`caps`/`thresholds`/`threshold_tiers`/
`exclusions`/`benefits`/`surcharges`/`reward_currencies`/
`redemption_routes` (per `supabase/migrations/0001_init.sql`, assembling
the same dict shape `engine/card_bundle.bundle_from_dict` already
consumes -- see #63) is the explicit next task once a reachable connection
string is confirmed, not attempted blind. `evaluation_runs`/
`evaluation_traces` row persistence is the same blocker, also deferred.

**RESOLVED 2026-08-12** -- Satya supplied a working connection string:
Supabase's session-pooler host (`aws-0-ap-southeast-1.pooler.supabase.com:
6543`, username `postgres.<project-ref>`) instead of the direct
`db.<ref>.supabase.co:5432` host that failed DNS resolution -- confirms
the IPv6-vs-IPv4 diagnosis. The database was already fully seeded (all 12
synthetic cards, published) from an earlier session outside this sandbox.
See entry #65 below for `PostgresCardRepository`'s build and verification.
`evaluation_runs`/`evaluation_traces` persistence remains unbuilt (not
needed by `/evaluate`'s current stateless response contract) and
`app/main.py`'s live default remains `SyntheticCatalogRepository` (#64,
still open -- switching it is a separate decision).

### 63. `engine/card_bundle.py` extracted from `tests/test_goldens.py`'s
five private adapter functions -- zero behaviour change, proven by the
unchanged 12 goldens

The golden battery's `_load_card_rules`/`_load_thresholds`/`_load_benefits`/
`_load_currencies`/`_load_exclusions`/`_load_surcharges` were the ONE
translation from a raw card dict into engine dataclasses, but lived as
test-only code. `/evaluate` needs that exact same translation (today from
`seeds/synthetic_cards.py`, tomorrow from Postgres per #62) -- rather than
writing a second copy (real risk of the two silently drifting in
interpretation, e.g. one adapter handling a selector field the other
forgets), moved it verbatim into `engine/card_bundle.py`
(`bundle_from_dict`/`currencies_from_dicts`) and had the test file's
`_load_*` functions become thin wrappers delegating to it. All 12 golden
tests pass completely unchanged -- the regression proof the extraction
didn't alter behaviour, not a rewrite.

`engine/evaluate.py`'s `evaluate_card` is the pipeline composition itself
(normalise -> eligibility -> match -> accrue -> caps/incremental-bands ->
thresholds -> apply_rule_activations -> valuation -> benefits -> costs ->
year-mode split -> assemble_nacv) -- exactly what every golden test's body
already ran by hand, consolidated into one call. `tests/
test_evaluate_orchestrator.py` runs it against all 12 synthetic cards and
asserts the output matches each golden's `expected` block exactly -- 13/13
pass on the first run, no numbers needed adjusting, which is the strongest
evidence the consolidation is faithful (not just "it runs", but "it
reproduces 12 independently hand-verified answers").

`POST /evaluate` and `POST /next-best-spend` (`app/main.py`) are thin
handlers over `evaluate_card` (CLAUDE.md rule 1: no financial math in API
handlers). Manually smoke-tested live (`uvicorn app.main:app`): a
`syn_ecom`-equivalent `/evaluate` POST reproduced `golden_syn_ecom_
basic.json`'s numbers exactly (gross reward ₹14,400, NACV steady-state
₹14,400, Year-1 ₹13,810, 3yr ₹42,610). `/next-best-spend`'s diminishing-
returns behaviour was hand-verified against `syn_ecom`'s own `cap_ecom`
kink (`tests/test_next_best_spend.py`): a ₹10,000 delta to ecommerce/
online nets a flat 5% (₹500, under the cap), a ₹3,00,000 delta nets only
4.2% (₹12,600 -- 12 months of 20,000 capped @5% + 5,000 overflow re-rated
@1% base, the exact `overflow: base_rate` mechanic `golden_syn_ecom_
basic.json` already verifies) -- the same kink `breakpoints.py` compiles
as a spend-domain breakpoint (Sbar = Cap/rate = ₹20,000/mo), demonstrated
here without depending on that module.

199/199 tests green (177 Phase 2 + 13 orchestrator-vs-golden + 6 API + 3
next-best-spend). Phase 3 complete for the synthetic-catalog path;
`PostgresCardRepository` (#62) is the carried-forward next task.

---

## 2026-08-12 -- `PostgresCardRepository` built and verified against the
live database, resolving #62

Satya supplied a working `DATABASE_URL`: Supabase's session-pooler host
(`aws-0-ap-southeast-1.pooler.supabase.com:6543`, username `postgres.
<project-ref>`) rather than the direct `db.<ref>.supabase.co:5432` host
that failed DNS resolution in #62 -- confirms that diagnosis (Supabase's
direct-connection endpoint needs IPv6; the pooler is IPv4-reachable).
`compute/.env` updated with the password percent-encoded (`#` ->
`%23`), matching the encoding the file's prior (unreachable) URL already
used.

### 65. `PostgresCardRepository` (`app/repository.py`) -- same translation,
different source, deterministic ordering added

Reads `cards`/`current_card_versions` (the published-and-effective-today
view, not a hand-rolled status/date filter) /`earning_rules`/`caps`/
`earning_rule_caps`/`thresholds`/`threshold_tiers`/`exclusions`/
`benefits`/`surcharges`/`reward_currencies`/`redemption_routes`, assembles
each card into the exact dict shape `seeds/synthetic_cards.py`'s `CARDS`
entries already have, and feeds it through the SAME `engine/card_bundle.
bundle_from_dict` translation `SyntheticCatalogRepository` uses (per #63's
whole point -- one translation, not two that could drift).

Every query got an explicit `ORDER BY key` (or `currency_id, key` for
routes) that the original design sketch in #62 hadn't specified -- without
it, Postgres row order is unspecified, which would make the repository's
own output non-deterministic between calls (CLAUDE.md rule 5: no
ordering dependence). `CardRuleBundle`'s tuple fields (`earning_rules`,
`caps`, `thresholds`, `exclusions`, `surcharges`) are order-sensitive for
equality even though nothing in the engine's own matching/evaluation logic
actually depends on their order (`match.py` picks winners by explicit
`priority`, never list position) -- ordering by `key` makes the repository
itself deterministic regardless, independent of whether any current
consumer happens to care.

Verified two ways against the live, already-seeded database (found fully
seeded from an earlier session outside this sandbox -- all 12 cards,
published, matching `seeds/synthetic_cards.py` exactly): (1)
`tests/test_postgres_repository.py` compares every field of all 12 cards'
`CardRuleBundle`s against `SyntheticCatalogRepository`'s, sorted-tuple
comparison for the order-sensitive fields (declaration order in the
Python list has no reason to match `ORDER BY key`, and isn't semantically
meaningful either way); (2) `evaluate_card` run against the
Postgres-sourced `syn_miles` bundle reproduces `golden_syn_miles_
vouchers.json`'s NACV exactly (steady-state ₹22,600, Year-1 ₹10,800, 3yr
₹56,000).

One expected, harmless mismatch surfaced by (1) before the resolution:
`redemption_routes.friction_default` is `NOT NULL DEFAULT 1.0` in the
schema, so the DB always materializes `friction=Decimal("1.0")` for a
route where the Python fixture leaves it implicit (`friction=None`,
meaning "use `engine/valuation.py`'s own `DEFAULT_FRICTION`"). Both are
computed identically by `_route_value_per_point` -- confirmed by (2)
producing byte-identical NACV output, not just asserted from reading the
formula. Structural comparisons in the test therefore skip the
`RewardCurrency`/`RedemptionRoute` dataclasses directly and rely on (2)'s
end-to-end proof instead.

`app/main.py`'s `get_repository()` still returns `SyntheticCatalogRepository`
unconditionally -- switching the live default (and deciding what happens
when `DATABASE_URL` is unset in some future deployment) is #64, a
separate, not-yet-made decision. 214/214 tests green (199 prior + 15 new
Postgres integration tests, skipped automatically when `DATABASE_URL`
isn't set/reachable so this never blocks `pytest` in an environment
without database access).

---

## 2026-08-12 -- `app/main.py`'s live default switched to
`PostgresCardRepository`, resolving #64

### 66. Fail loudly on a configured-but-unreachable `DATABASE_URL`, not
silently on synthetic data

`get_repository()` now returns `PostgresCardRepository(database_url)`
whenever `DATABASE_URL` is set at all, falling back to
`SyntheticCatalogRepository` only when it's unset entirely. Deliberately
did NOT make it "try Postgres, fall back to synthetic on any connection
error" -- a deployer who configured a database expects `/evaluate` to
serve it; silently substituting the synthetic fixture cards on a
connection failure would mask a real misconfiguration behind numbers that
merely look plausible (the synthetic and live catalogs happen to hold the
same 12 cards today, which is exactly what would make the substitution
invisible). `PostgresCardRepository.__init__`'s own `psycopg.connect`
already raises on failure; that propagates as-is.

Load order matters here: `compute/.env` is loaded via `python-dotenv` at
module import time (`load_dotenv()`, which never overwrites an
already-set environment variable, so a real deployment's own env vars
still win over a stray `.env` file) -- before `get_repository` is ever
called, since it's only invoked lazily per-request via FastAPI's
`Depends`, not at import time.

`app/main.py`'s prior `@app.on_event("shutdown")` handler (used to close
the Postgres connection on shutdown) turned out to be deprecated in the
installed FastAPI (0.141) -- switched to the `lifespan` async context
manager FastAPI's own docs now recommend, same effect (closes the
connection if a `PostgresCardRepository` was ever constructed, no-ops
otherwise).

### 67. `tests/test_api_evaluate.py` explicitly overrides `get_repository`
to `SyntheticCatalogRepository`

Switching the live default meant `tests/test_api_evaluate.py`'s
`TestClient(app)` would otherwise start hitting the real, network-
dependent database on every test run purely because `compute/.env` now
has a working `DATABASE_URL` -- silently turning a fast, deterministic
test suite into a slow, flaky, network-dependent one, exactly the failure
mode `tests/test_postgres_repository.py` was deliberately isolated to
avoid. Fixed with FastAPI's own `app.dependency_overrides[get_repository]
= SyntheticCatalogRepository` at module scope -- the standard, supported
way to pin a test's dependencies regardless of the app's runtime
configuration, rather than relying on `DATABASE_URL` happening to be
unset in whatever environment the tests run in.

Verified live end-to-end: `POST /evaluate` against a running `uvicorn
app.main:app` (with `DATABASE_URL` set, no override) for `syn_miles`
reproduced `golden_syn_miles_vouchers.json`'s NACV exactly (steady-state
₹22,600, Year-1 ₹10,800, 3yr ₹56,000) -- served from Postgres this time,
not the synthetic catalog, confirming the switch actually took effect and
not just that the code compiles. 214/214 tests green, same count as
before (the fix reshuffled which repository each suite exercises, added
no new test files).

---

## 2026-08-12 -- Phase 4 slice 1: `optimiser/allocate.py`, the inner MILP
for a fixed card subset (Part B SS B.2-B.4, Part E SS E.4)

### 68. Scope split confirmed with Satya: `allocate.py` alone this pass,
not all 8 of Part E SS E.0's optimiser modules

Part E lists 8 new modules (`candidates.py`, `enumerate.py`,
`allocate.py`, `repair.py`, `frontier.py`, `classify.py`, `scenarios.py`,
`explain.py`). `allocate.py` is the true dependency root -- `candidates.
py`'s standalone-value pre-filter and `enumerate.py`'s per-subset solve
both call it directly, `repair.py` consumes its output alongside `engine.
evaluate.evaluate_card` -- the same role `engine/accrue.py` played inside
Phase 2's stage-by-stage build. Confirmed roadmap for the remaining 7,
build order: `repair.py` -> `enumerate.py` -> `candidates.py` ->
`frontier.py` + `classify.py` -> `scenarios.py` -> `explain.py` +
`/optimise`.

This slice builds continuous variables only (B.2's `x(c,k,t)`, `s(c,q,t)`)
for a subset the CALLER supplies (no card-selection binary `y` -- B.6/E.4's
own "y removed" framing for the inner problem) plus the always-available
outside option `c0` (A.11). Concave capped/accelerated curves (B.5) are
the one nonlinearity a plain LP already handles correctly by construction
(a maximising LP fills the higher-rate segment first automatically).
Everything needing a binary -- milestones `z`, waiver `w`, fees, benefit
dedup `l`, cardinality/user-constraints, and B.5's convex incremental-tier
fill-order binaries -- is the explicit next increment, same "deliberately
narrow first" posture as `caps.py`'s own initial build (#6).

### 69. `k` generalised to (category, channel, geography, merchant_group);
ê (planning rate) kept distinct from `caps.py::flat_rate` (evaluator-exact rate)

Part A/B's notation treats `k` as a single "spend category" dimension, but
Part C's rules already differentiate on channel/geography/merchant_group
too (`syn_upi`'s `channels=[upi]` rule, `syn_points`'s `merchant_groups=
[synth_portal]` rule) -- modelling `k` as literally just category would
under-represent rules the engine already supports and goldens already
exercise. `allocate.py`'s `SpendKey` generalises `k` to the full tuple,
matching `engine.normalise.SpendSegment`'s own identity exactly.

Promoted two private stage helpers to public, same "promote when a later
stage needs it" move as `window_instances`/`window_flags` (#15):
`engine.accrue.effective_rate(accrual, ticket_size)` (Part A.2's ê,
refactored out of `_ticket_approx_reward`, no behaviour change -- existing
accrue tests pass unchanged) and `engine.valuation.
primary_route_value_per_point(currency, primary_route_key)` (deliberately
bypasses the `min_points` eligibility gate `value_currency` applies for a
*specific* realized point total -- the optimiser's planning rate needs a
route's per-point value unconditionally, not zeroed out because a probe
amount happens to fall under a transfer route's minimum). Also promoted
`engine.evaluate._assumptions_snapshot` to `assumptions_snapshot_from`,
now shared between `evaluate_card` and `allocate` rather than duplicated.

`ê` is deliberately NOT `caps.py::flat_rate` (the evaluator-exact
continuous rate `breakpoints.py` uses for spend-domain threshold
conversion -- a different purpose, exact crossings, not approximated
planning value). Cap width for a capped segment is `Cap.amount / ê`, using
the SAME ê as the segment's own rate -- self-consistent (`ê × width =
Cap.amount` exactly at the boundary), which mixing in `flat_rate` would
break.

### 70. Segment compilation reuses Stages 1-3 wholesale; three scope
restrictions this pass, each raised rather than silently mismodelled

Key design choice: which rules bind to a given (category, channel,
geography, merchant_group) doesn't depend on how much is allocated there
(rule selection is selector/priority-based, not amount-based), so
`_compile_card_pools` runs the REAL `normalise -> apply_eligibility ->
match` pipeline per candidate card (as if its full declared demand were
routed there) rather than re-deriving winner-takes-all/stacking
resolution -- the only way this module can't silently diverge from the
evaluator's own interpretation of a card's rules. A stacking rule and the
non-stacking winner are independent segment pools that both reference the
same `x(c,k,t)` (B.4(3)'s "per rule-group" framing), not one shared pool.

`overflow: base_rate`'s fallback rate is found the same way `caps.py::
_apply_one_cap` finds it (`match_segment` against rules outside the cap's
pool, take the non-stacked winner) -- replicated rather than imported
since this pass only supports `scope="rule"` pools (pool = `{cap.rule_key}`
trivially), not `caps.py`'s general `_scope_rule_keys` resolution.

Three restrictions raise a clear `ValueError` rather than being silently
mismodelled, none of which exclude anything in the current 12-card
catalog: reward caps with `scope` other than `"rule"` (`rule_group`/`card`
pooling -- `cap.py`'s `_scope_rule_keys` would need promoting, deferred to
whichever increment first needs it); reward caps on non-monthly windows
(quarterly/annual caps need a segment variable pooled across months this
slice doesn't build -- every reward-measure cap in the seed catalog
happens to be monthly today); `tier_mode="incremental"` rules (`syn_slab`
-- B.5's convex PWL case needs fill-order binaries).

### Verification -- three hand-computed scenarios (`tests/
test_allocate.py`), all correct on the first real solve, no debugging needed

1. Single card (`syn_ecom`, the SAME spend as `golden_syn_ecom_basic.
   json`) -- `reward_value` = ₹14,400.00, byte-for-byte the golden's
   evaluator-verified number. No allocation decision is being made here
   (one card, all demand forced there); this proves segment/rate/cap-width
   compilation is correct on its own.
2. Two cards (`syn_ecom` + `syn_flat`), ₹6,00,000/yr ecommerce/online --
   B.5's "concave curve fills the higher rate first" put to a real
   cross-card test: ₹20,000/mo fills `syn_ecom`'s capped 5% segment
   (₹12,000/yr) exactly, the remaining ₹30,000/mo correctly routes to
   `syn_flat`'s flat 1.5% (₹5,400/yr) rather than `syn_ecom`'s own 1%
   overflow -- total ₹17,400.00/yr, matching the hand computation exactly.
3. Outside option (`syn_fuel`, ₹6,00,000/yr fuel spend, double the
   ₹25,000/mo cap-equivalent width) -- confirms A.11's claim ("the
   optimiser routes surcharge-negative spend away from cards
   automatically") is actually true of this implementation, not just spec
   prose: exactly ₹3,00,000/yr routes to `syn_fuel` (the positive-margin
   portion, net +0.32%/rupee) and ₹3,00,000/yr routes to `c0` (the
   overflow portion once `fuel_refund`'s cap binds, net -0.68%/rupee) --
   `pv_planned` = ₹960.00/yr, matching the hand computation exactly.

Plus a forced-CBC-fallback test (same answer as HiGHS) and a
`tier_mode="incremental"` `ValueError` test. `PULP_CBC_CMD` kept over its
suggested replacement `COIN_CMD` after verifying directly in this
environment that `COIN_CMD` can't locate `cbc.exe` (`PulpSolverError`)
while `PULP_CBC_CMD` works -- a deprecation warning is an acceptable
trade for a fallback path that's actually verified to run, not merely
assumed to. `pulp.LpVariable(...)` direct construction (also deprecated)
switched to `prob.add_variable(...)` throughout, eliminating 324 of 325
deprecation warnings the first draft produced.

219/219 tests green (214 prior + 5 new).

---

## 2026-08-12 -- Phase 4 slice 2: `optimiser/repair.py`, exact evaluation +
near-miss threshold repair (Part E SS E.1 steps 5-6, SS E.7; Part A SS
A.16; Part B SS B.10.4)

### 71. E.7 splits into two independently-buildable parts; A.16/B.10.4's
"fix the binaries" framing doesn't apply yet because there are no binaries

A.16/B.10.4 describe the repair rule as "fix milestone/waiver binaries to
their evaluator-verified states, re-allocate once" -- language that
presumes the MILP already models milestones/waivers as binaries `z`/`w`.
Slice 1's LP doesn't (docs/DECISIONS.md #68) -- continuous variables only,
by design. Rather than block on that (or worse, silently build a
"repair" that doesn't actually repair anything meaningful), re-read E.7's
own two-part description directly: "(1) run the engine on the MILP
allocation -> pv_exact... (2) compile the breakpoint list, generate near-
miss/barely-made variants, evaluate, keep the max." Part (1) is E.1 step
5 (EVALUATE) and needs nothing from the LP's own structure -- just a
translation from `allocate.py`'s `x(c,k,t)` solution to `evaluate_card`
calls. Part (2) uses `engine/breakpoints.py` (already built, Phase 2),
which compiles from a card's *rules* directly, independent of whatever
the LP's objective does or doesn't model.

Realized while designing this: because slice 1's LP has literally zero
milestone/waiver terms in its objective, it is *structurally blind* to
thresholds -- its allocation has no reason whatsoever to land near one.
The near-miss variant search is therefore not a refinement of an
otherwise-complete optimiser right now; it is currently the *only*
mechanism in the whole pipeline that can capture threshold value at all.
That will flip once a future slice adds milestone/waiver binaries
directly to the LP (at which point near-miss becomes a genuine refinement
on top of an already-threshold-aware allocation) -- but it's the load-
bearing piece today, which argued for building it now rather than later.

### 72. Three scope narrowings for this pass, each a real design surface
deferred rather than an oversight

- **Near-miss only, not "barely-made."** Barely-made means pulling back
  spend that's just cleared a *cap* boundary in case it earns more
  elsewhere -- but `allocate.py`'s LP already gets this right by
  construction (B.5: a maximising LP fills the higher-rate segment
  first, automatically deciding how much spend should sit past a cap).
  It's specifically *threshold* breakpoints the LP can't see, and
  thresholds only ever add value once crossed (never make crossing them
  worse) -- so near-miss (push under-threshold spend over) is the case
  with real LP-invisible upside; barely-made-past-a-milestone (pull back
  spend that already earned a milestone, hoping the freed spend does
  better elsewhere) is a legitimate but secondary refinement.
  `repair.py` doesn't even compile cap breakpoints (`CardBreakpointInputs
  .caps` left empty) -- consistent with the same reasoning.
- **Top-up sourced from `c0` only.** Moving spend FROM the outside option
  is always safe (it earns nothing, so there's no opportunity cost to
  weigh) -- matches C.0's own "top-up... spend to cross T(beta))"
  phrasing exactly. Pulling from a real card's allocation instead needs a
  cost model for what's given up there -- the same kind of model barely-
  made would need, deferred alongside it.
- **Full cover or nothing.** A near-miss variant is only constructed when
  `c0` can cover the *entire* gap -- a partial top-up would leave the
  threshold uncrossed (thresholds are step functions, no partial credit)
  while still taking on the small opportunity cost of having moved spend
  at all, so it can only ever be neutral-to-harmful. Never worth
  evaluating.

### Design and verification

`evaluate_allocation(bundles, currencies, allocation, assumptions)`
groups an `AllocationResult`'s `SpendAllocation` entries by card and by
`SpendKey`, reconstructs a `CategorySpend` with a seasonality vector
derived from the *actual* per-month amounts (`engine.normalise._allocate`'s
own paisa-exact residual reconciliation guarantees this round-trips to
the exact original monthly split), and runs each card through Phase 3's
unchanged `evaluate_card` -- the only place this module computes a rupee
value (CLAUDE.md rule 1). `repair(...)` calls this once for the baseline,
then for every threshold breakpoint on every card (via `engine.breakpoints
.compile_card_breakpoints`, reused unchanged) and every window instance
(`engine.caps.window_instances`, reused) checks whether current pooled
spend -- computed by building real `SpendSegment`s from the allocation and
running them through `engine.eligibility.apply_eligibility` (reused;
exactly Stage 2's milestone/waiver view split) -- falls within `buffer(beta)`
short of the threshold. Each in-range breakpoint gets its own independent
top-up variant (never chained onto a previous variant), evaluated and kept
only if it beats the current best -- matching E.7's "~60 extra evaluator
calls per subset" budget (per-breakpoint, not combinatorial).

Verified with `tests/test_repair.py`, allocations constructed directly
(not solver-emergent, for the same reason slice 1's own tests used hand-
picked spend rather than hoping `allocate()` happens to produce a
near-miss): `syn_ecom` at ₹96,000/yr grocery (₹4,000 short of its
₹1,00,000 waiver threshold, buffer ₹5,000) with ₹4,000/mo dining parked
on `c0` -- baseline NACV ₹370.00 (fee charged), repaired NACV ₹1,000.00
(waiver crossed, fee waived), `repair_applied=True`. A no-`c0`-supply
variant and a beyond-buffer variant both correctly produce zero variants
tried. A real `allocate()` solve (slice 1's own scenario 1) round-tripped
through `evaluate_allocation` reproduces `golden_syn_ecom_basic.json`'s
₹14,400.00 exactly, proving the allocation-to-evaluator translation
itself is correct, not just `allocate()`'s internal accounting.

223/223 tests green (219 prior + 4 new).

---

## 2026-08-12 -- Phase 4 slice 3: `optimiser/enumerate.py`, subset
generation (Part E SS E.3)

### 74. Full-sweep enumeration only; five follow-up concerns named and
deferred rather than half-built

`enumerate.py` is pure orchestration over slices 1-2 (`itertools.
combinations` + `allocate` + `repair` per subset) -- no new financial
logic (CLAUDE.md rule 1). Five things SS E.3 describes are deliberately
not attempted this pass, each because the machinery it would sit on top
of doesn't exist yet, not because they were forgotten:

- **Wallet mode's mandatory current-portfolio inclusion.** Wallet mode
  itself doesn't exist (#10/#61) -- greenfield enumeration only.
- **Structural infeasibility filtering** (fee budget on unwaivable fees,
  network requirements, must-keep/refuse-use). Part B SS B.4(8)'s
  user-rule constraints aren't modelled anywhere -- `allocate.py` takes a
  subset as a given input, no `y`/constraint machinery exists to check
  against. Every subset is tried.
- **Bound pruning** (`UB(S) = SUM(BestCaseNACV(c)) - fees`, skip when
  `< 0.9 * best_exact_so_far`). Needs a MABC-style per-card ceiling (SS
  E.2's job, not built) -- and the spec itself frames this as a scale
  optimisation ("exists for the day the candidate cap rises... never
  prunes anything within 10% of the lead"), irrelevant at today's <=12
  synthetic cards. Full-sweep (no pruning) is already a spec-sanctioned
  mode on its own, not a workaround.
- **Caching** (`subset_key + spend hash + rule versions + assumptions
  version`). Needs `portfolio_subset_results` persistence, which needs
  `DATABASE_URL`-backed writes nothing currently does (same blocker
  `evaluation_runs`/`evaluation_traces` persistence has, #62's entry).
- **Parallelism.** A performance concern, not correctness; sequential is
  fine at today's candidate-set sizes.

`cardinality_mode` validated against the exact three-value vocabulary
`supabase/migrations/0001_init.sql`'s `optimisation_runs` table already
uses (`'exactly' | 'up_to' | 'optimiser_decides'`) rather than inventing
a parallel one, and `subset_key` built the same way
(`portfolio_subset_results.subset_key`'s documented convention: sorted
card keys, `"+"`-joined) -- so a future DB-writing pass can adopt this
module's output directly, no format translation needed.

### Verification

`tests/test_enumerate.py`, `{syn_ecom, syn_flat}` (the same two cards and
spend as `tests/test_allocate.py`'s own scenario 2, Rs6,00,000/yr
ecommerce/online): `up_to` with `max_cards=2` produces exactly the 3
expected subsets (`C(2,1)+C(2,2)`), each cross-checked -- `{syn_ecom}`
alone hand-computes to Rs15,600.00/yr (no `syn_flat` alternative this
time, so the Rs30,000/mo overflow past the cap re-rates to syn_ecom's own
1% base rather than diverting anywhere), `{syn_flat}` alone to
Rs9,000.00/yr (flat 1.5%, no fee), and the 2-card subset reproduces slice
1's own Rs17,400.00/yr exactly via the enumeration path -- confirming it
really is the best of the three, setting up cleanly for `frontier.py`
later without building it now. `"exactly"` mode with `max_cards=1`
correctly returns only the two size-1 subsets, no pair. An unknown
`cardinality_mode` raises, matching the engine's existing posture on
unknown vocabulary elsewhere.

227/227 tests green (223 prior + 4 new).

---

## 2026-08-12 -- Phase 4 slice 4: `optimiser/candidates.py`, pre-filtering
(Part E SS E.2, Part B SS B.7)

### 76. Builds SS B.7's two-part coverage guarantee (standalone + category
champions), defers SS E.2's MABC and hard include/exclude

SS B.7 defines pre-filtering as a union of exactly three things: top-N
standalone value, per-category champions, and user-constraint-required
cards, with an explicit warning about the first alone: "naive pre-
filtering by standalone value is biased -- it drops specialist cards...
whose standalone value is low but whose incremental value inside a
portfolio is high." Standalone + champions together are what actually
delivers that guarantee; this pass builds both. Deferred, each because
the machinery it needs doesn't exist yet:

- **MABC** (SS E.2 step 5) -- "for each threshold tier of each card,
  exact-evaluate the card with eligible spend forced to the tier." SS
  B.7 itself doesn't mention MABC at all -- it's SS E.2's own later
  addition on top of the core spec, needing a "force eligible spend to
  exactly this tier" evaluation construct nothing builds. A real,
  legitimate refinement (catches a card whose value comes from a big
  milestone jump rather than a strong steady per-rupee rate), but
  additive to the coverage guarantee, not part of it.
- **Hard include/exclude** (wallet cards, must-keep, refuse-use, "at
  least 1 RuPay") -- needs wallet mode and user-constraint machinery,
  neither of which exists (same blocker as #73).
- **`optimisation_runs.constraints_snapshot` persistence** -- needs DB
  writes nothing does yet (#62/#73's same blocker). The "why was card X
  considered" explainability SS E.2 asks for is still produced --
  `CandidateSelection.standalone_ranked` and `.champions` carry it --
  just returned in-memory rather than written to a row.

### 77. Standalone value MUST go through `allocate`+`repair`, not a raw
`evaluate_card` call; category-champion marginal rate is a true delta,
not an average, specifically so the fixed fee cancels out

Two design choices worth recording rather than picking silently:

**Standalone value uses the LP.** SS B.7 itself calls this "a cheap
single-card LP, no enumeration" -- not simply "evaluate the card with all
spend forced onto it." The reason surfaced concretely from `tests/
test_allocate.py`'s own `syn_fuel` scenario (Phase 4 slice 1): a single
card's TRUE standalone value can route part of the user's spend to `c0`
when that card's margin on some category is negative (there, fuel spend
past `fuel_refund`'s cap, net -0.68%/rupee after its own surcharge).
Forcing all spend onto the card regardless would understate standalone
value for any surcharge- or negative-margin-heavy card. `_standalone_
value` therefore calls `allocate([bundle], ...)` then `repair([bundle],
...)`, exactly the single-card-subset path slices 1-2 already proved
correct, not a shortcut around it.

**Category-champion rate is `MV(c,k,delta) = Evaluator(baseline+delta) -
Evaluator(baseline)` (Part A SS A.15's own formula), never `NACV(category
spend) / category spend`.** The card's fixed annual fee is identical in
both evaluations (assuming `delta` doesn't itself cross a waiver
threshold, which the Rs10,000 default is chosen small enough to avoid in
every scenario this pass's fixtures exercise) and cancels exactly in the
subtraction -- an average-inclusive-of-fixed-costs rate would instead
make ANY card with an unwaived annual fee look artificially worse at
small category amounts, exactly the kind of smoothed-number bias SS E.2
insists candidate selection avoid ("steps 3-5 all use the exact
evaluator, so no card is ever excluded on smoothed numbers"). `delta =
Rs10,000` reuses Phase 3's `/next-best-spend` delta-band convention
rather than inventing a new one. This is a direct `evaluate_card` call,
no `allocate`/`repair` needed -- evaluating one category on one card in
isolation is not a cross-card allocation decision, so there's nothing for
an LP to decide; a negative marginal rate is itself a valid,
correctly-informative "this card is a bad specialist pick for this
category" signal, not a case needing special handling.

### Verification

`tests/test_candidates.py`, universe `{syn_ecom, syn_flat, syn_miles}`,
spend ecommerce/online Rs6,00,000/yr + utilities Rs30,000/yr (both clear
the Rs25,000 champion threshold). Standalone values hand-computed exactly:
`syn_ecom` Rs15,900.00, `syn_flat` Rs9,450.00, `syn_miles` Rs1,350.00
(reward Rs3,150 via the "stmt" route + milestone Rs10,000 for the
4,00,000 tier, minus a steady fee of Rs11,800 since this card has no
waiver threshold at all). Champions on both categories: `syn_flat`
(1.5%, flat, unconditional) then `syn_ecom` (1% -- notably NOT its 5%
accelerated rate: at a Rs6,00,000 baseline the marginal Rs10,000 already
lands in syn_ecom's own overflow zone, past its own Rs20,000/mo cap-
equivalent), `syn_miles` never makes the top-2 (0.5%, well behind both).
Two scenarios use an artificially tight `standalone_n`/`max_total`
specifically to make the union/trim mechanics visible with real numbers
(the current 12-card catalog's economics don't happen to produce a
"hidden specialist beats the generalists outright" story --
`syn_flat`'s fee-free flat 1.5% is simply strong across the board in this
fixture set, a property of the fixtures rather than a gap in the
algorithm): `standalone_n=1` still rescues `syn_flat` into the final
candidate set via the champions union; `standalone_n=3, max_total=2`
trims exactly `syn_miles` (the only standalone-only, lowest-ranked entry)
while protecting both champions.

231/231 tests green (227 prior + 4 new).

---

## 2026-08-12 -- optimiser/frontier.py + optimiser/classify.py (Part E SS E.8-E.9)

### 78. `optimiser/enumerate.py`'s `SubsetResult` gained a `card_results` field

`repair.RepairResult.valuation.card_results` (per-card `EvaluateResult`,
already computed by every `repair()` call) was being computed then thrown
away by `enumerate_subsets` -- only `pv_exact`/`gap` were kept. Frontier's
T2 (fee-cover ratio) needs each card's `gross_reward_value`/
`milestone_value`/`benefit_value` to compute `DeltaGrossBenefit`, so
`SubsetResult` now carries `card_results: dict[str, EvaluateResult]`
too -- zero new computation, just stops discarding data already produced.
Purely additive (new field, no signature change to any existing call
site); `tests/test_enumerate.py`'s 5 existing tests pass unchanged.

### 79. T2's `DeltaF` is a portfolio-total fee delta, not literally "the new
card's fee" -- because the frontier gives no nesting guarantee

SS E.9's own worked example ("3rd card: ... fees +Rs1,500 ...") reads as
if step n->n+1 always adds exactly one card on top of the same n-card
base. But the frontier's winning subset at size n+1 is `SELECT size,
max(pv_exact) ... GROUP BY size` -- an entirely different (n+1)-combination
can simply score higher than "the size-n winner plus one card," with no
guarantee the size-n winner is even a subset of it. Full-sweep enumeration
(SS E.3) doesn't produce a nested chain by construction. Resolved as
`DeltaF = TotalGrossFee(winner(n+1)) - TotalGrossFee(winner(n))` -- well-
defined regardless of nesting, and identical to the single-card reading
whenever the frontier IS nested (the common case in practice), so nothing
is lost when it is nested and nothing breaks when it isn't.

### 80. "Gross, pre-waiver" fee = `annual_fee . (1+GST)`, not the bare
sticker price

SS E.9's T2 says "ΔF = additional annual fees committed (gross,
pre-waiver)" -- explicit about ignoring the waiver, silent on GST.
Read as "the amount you're actually on the hook for absent a waiver,"
matching `engine.costs.compute_fees`'s own `steady_fee` formula with the
waiver term forced off (`annual_fee * (1+GST_RATE)`), not the pre-tax
number -- GST is a real, unavoidable cost of NOT getting the waiver, so
excluding it would understate what "fees committed" actually means to the
user.

### 81. `DeltaGrossBenefit` = NACV with fees/forex/surcharge added back, not
NACV itself

`engine.assemble.assemble_nacv`'s formula is `NACV = GrossReward +
MilestoneValue + BenefitValue - SteadyFee - ForexCost - SurchargeCost`.
T2's "ΔGrossBenefit / ΔF >= 1.5" reads as a margin-thickness check --
does the reward SIDE alone cover the fee several times over -- not a
net-of-everything number (that's what T1/ΔV already checks). Implemented
as `Sum_c (gross_reward_value(c) + milestone_value(c) + benefit_value(c))`
across the subset's `card_results`, deliberately excluding forex/surcharge
too (those are costs, not part of "gross benefit" under any reading).

### 82. New assumption-registry defaults -- NEW, needs Satya's sign-off

SS E.9 itself: "all three [T1/T2] parameters live in the assumptions
registry (C.7), never hidden." No C.7 section actually lists numeric
values for these (C.7 as read so far covers ticket sizes and the UPI mix,
not optimiser-level tolerances), so the spec's own suggested defaults were
implemented as-is, exposed as keyword arguments on `build_frontier`
(same "module-level DEFAULT_* constant, override via kwarg" pattern as
`candidates.py`'s `DEFAULT_STANDALONE_N` etc.):

| Constant | Value | Source |
|---|---|---|
| `abs_floor` | Rs2,000/yr | SS E.9 T1, stated directly |
| `rel_pct` | 3% | SS E.9 T1, stated directly |
| `fee_cover_ratio` | 1.5x | SS E.9 T2, stated directly |
| `fee_de_minimis` | Rs1,000 | SS E.9 T2, stated directly |
| `icv_meaningful` (classify.py) | Rs1,000/yr | SS E.8, stated directly ("registry, default Rs1,000") |

Unlike #1's `upi_category_mix` (an outright guess), these five are all
literal numbers already in the spec text -- flagged here per CLAUDE.md's
"surface every assumption-registry default" rule anyway, since they're
still registry values a future UI must let the user edit, not constants
this module should own.

### 83. `n_tol=None` means "no user-specified tolerance," not "tolerance 0"

SS E.9 frames `N_tol` as "the user's complexity tier" -- a UI concept that
doesn't exist as an input anywhere yet (no wallet/constraint model, same
family of gaps as #73/#75/#76). `build_frontier(..., n_tol=None)` (the
default) never triggers `capped_by_tolerance` -- the walk is bounded only
by T1/T2/T3 and by how far the sweep enumerated. A future caller wires the
user's actual tier through this same parameter; nothing changes on this
module's side when that lands.

### 84. `classify.py`'s `_pv_of` reuses SS E.8's own escape hatch ("if
enumerated; else one extra solve") rather than requiring a full sweep

Every ICV/Overlap lookup classify.py needs (`P`, `P\{c}`, `{c}` standalone,
`P u {c+}`, family-swap subsets) is first looked up in the caller's
already-enumerated `results`; on a miss, it calls `allocate()` + `repair()`
directly on exactly that subset -- the same two primitives
`optimiser/enumerate.py` and `optimiser/candidates.py` already build on.
This means classify.py works correctly even against a narrow
`cardinality_mode="exactly"` sweep that never enumerated the size-(n-1)/
size-1 subsets classification needs (verified directly:
`test_pv_falls_back_to_a_fresh_solve_when_not_in_results` supplies only
the two single-card results and confirms the 2-card portfolio value is
still solved correctly, landing on the exact same Rs17,850.00 the
lookup-path test gets independently).

### 85. DOWNGRADE and HOLD are spec-complete but untested against a real
fixture -- confirmed no schema gap was silently papered over

Grepped the whole repo for `family_key` before writing classify.py: zero
hits outside Part E's own prose. `cards.family_key` (Part D SS D.3's "small
additive schema element") was never added to `seeds/synthetic_cards.py`,
`supabase/migrations/`, or `engine/card_bundle.py`. Rather than inventing
a family relationship among the 12 synthetic cards that doesn't reflect
anything real, DOWNGRADE is implemented exactly to SS E.8's formula
(`pv_exact(P\{c} u {c'}) > pv_exact(P)`) gated on an optional
caller-supplied `family_keys: dict[card_key, family_id]`, defaulting to
`{}` (DOWNGRADE never fires). Tested with a directly-fabricated
`SubsetResult` pair proving the comparison logic itself is correct, same
posture as `WelcomeValue` (#29) and `flat_perk` (#23) -- "spec-complete,
no real fixture yet" is recorded, not silently absent. HOLD's
`strategic_feature_cards` flag is the same story: no user-constraint model
exists to derive "the user cares about this card's zero-forex feature"
from, so it's a plain caller-supplied set, tested the same fabricated way.

### Verification

`tests/test_frontier.py` (10 tests): frontier points and the T1/trivial-T2
path verified end-to-end through the real engine (syn_ecom + syn_flat,
Rs12,00,000/yr ecommerce -- syn_ecom alone Rs21,600.00, syn_flat alone
Rs18,000.00, both Rs26,400.00, DeltaV=Rs4,800.00 clears T1's Rs2,000 floor);
a smaller-spend variant (test_enumerate.py's own Rs6,00,000 scenario,
DeltaV=Rs1,800.00) proves T1 can genuinely block a step; `n_tol=1` proves
tolerance-capping without discarding the step record; a fabricated
size-1/size-3 gap proves the walk stops rather than guessing across a
missing size. T2's actual ratio arithmetic and T3's optional veto are
tested against directly-constructed `SubsetResult`s (ratio 1.695x passes,
0.678x fails above the Rs1,000 de-minimis; a negative low-spend delta
vetoes an otherwise-passing step; an unmatched subset key leaves `t3_pass`
`None` rather than defaulting either way). `format_step` checked
ASCII-only and stating the right numbers.

`tests/test_classify.py` (7 tests): KEEP/OPTIONAL/ICV/Overlap verified
end-to-end (universe `{syn_ecom, syn_flat, syn_miles}`, test_candidates.py's
own spend and standalone numbers -- P={syn_ecom,syn_flat} pv_exact
Rs17,850.00, ICV(syn_ecom|P)=Rs8,400.00, ICV(syn_flat|P)=Rs1,950.00, both
Overlaps Rs7,500.00); the fresh-solve fallback verified to land on the
identical Rs17,850.00 with only single-card results supplied; ADD/
NOT_MATERIAL verified via a fabricated 3-card lookup (isolating classify's
own arithmetic from a full milestone-crossing hand computation already
covered elsewhere); CLOSE/HOLD and DOWNGRADE verified via fabricated
subsets per #85 above.

248/248 tests green (231 prior + 17 new).

---

## 2026-08-12 -- optimiser/scenarios.py (Part E SS E.11)

### 86. Whole-vector scalar scaling; `UpiAggregateSpend` scaled too, not just
`CategorySpend`

SS E.11's formula is literally "spend vector x {0.8, 1.0, 1.2}" -- the
whole vector, not just the category-mode lines. `SpendInput` has two
distinct spend paths (`category_spend` tuple and the optional
`upi_aggregate: UpiAggregateSpend`, Stage 1's C.4.1 decomposition input);
`scale_spend` multiplies both when present, so a user who declared their
spend as an aggregate UPI figure gets correctly-scaled Low/High scenarios
too, not silently unscaled ones.

### 87. Robustness is `None`, not 0 or a negative/absurd ratio, when
`V_expected <= 0`

Not spec-stated either way. `Robustness = V_low/V_expected` is meant to
answer "what fraction of its value does this portfolio keep if spending
drops" -- a question that only makes sense when there's positive value to
begin with. A portfolio the optimiser would never actually recommend
(net-negative expected NACV) doesn't get a fabricated percentage;
`robustness_for` returns `None` for exactly this case, distinguishable
from an actual computed ratio the same way `frontier.py`'s `t3_pass:
bool | None` is (`None` = "not a meaningful number here," not "zero").

### 88. Rank stability computed across every enumerated subset, not
grouped by size

SS E.11: "rank stability (does the recommended portfolio stay top-3
across scenarios)" -- no size-grouping qualifier. `_rank` sorts ALL of a
scenario's `SubsetResult`s by `pv_exact` and finds where the target
subset lands; `rank_stable` requires that rank `<= top_n` (default 3, SS
E.11's own number) in Low, Expected, AND High simultaneously. This means a
2-card portfolio is being ranked against 1-card and 3-card portfolios
too, not just other 2-card ones -- which is exactly SS E.9's own frontier
comparison scope (the frontier picks a best-of-all-sizes winner, so
"stays top-3" naturally means top-3 among everything the sweep
considered, the same population frontier.py's `build_frontier` draws its
per-size winners from).

### 89. `run_scenarios(expected_results=...)` lets a caller skip the third
solve

Every real caller of this module will already have run the expected-spend
sweep (frontier.py needs `enumerate_subsets`'s output regardless of
whether scenarios ever run) -- re-solving it a third time inside
`run_scenarios` would be pure waste. `expected_results` is optional
(defaults to `None`, which triggers a fresh solve so the module is usable
standalone/in tests without ceremony); when supplied, it's passed through
by identity, not copied or re-validated. `test_expected_results_reused_
instead_of_resolved` asserts `sweep.expected is precomputed` specifically
to prove no silent re-solve happens.

### 90. New assumption-registry defaults -- NEW, needs Satya's sign-off

Same posture as #82 (frontier.py's T1-T3 constants): all three numbers
are stated directly in SS E.11's own text, not guessed, but still flagged
per CLAUDE.md's "surface every assumption-registry default" rule since a
future UI must let the user edit them.

| Constant | Value | Source |
|---|---|---|
| `low_factor` | 0.8 | SS E.11, "spend vector x {0.8, 1.0, 1.2}" |
| `high_factor` | 1.2 | SS E.11, same |
| rank-stability `top_n` | 3 | SS E.11, "stay top-3 across scenarios" |

### Verification

`tests/test_scenarios.py` (7 tests). `scale_spend` checked against both
`CategorySpend` and `UpiAggregateSpend` paths. Low/Expected/High swept
end-to-end through the real engine on `{syn_ecom, syn_flat}` at
Rs6,00,000/yr (Low Rs4,80,000, High Rs7,20,000): syn_ecom alone
Rs14,400.00/Rs15,600.00/Rs16,800.00, syn_flat alone Rs7,200.00/
Rs9,000.00/Rs10,800.00, both Rs15,600.00/Rs17,400.00/Rs19,200.00 -- the
Expected-spend figures match test_enumerate.py's own independently-
verified numbers exactly, confirming `run_scenarios`'s 1.0x sweep isn't
silently diverging from a plain `enumerate_subsets` call. A second
scenario (Rs12,00,000/yr, matching test_frontier.py's own T1-pass fixture)
verifies `robustness_for`'s ratio arithmetic and wires `low_spend_pv_by_
subset_key` straight into `build_frontier`, confirming T3 actually
receives and correctly applies real Low-scenario numbers end-to-end (not
just structurally, as test_frontier.py's own T3 tests already checked
with fabricated data). Rank-stability's "drops out of top-3" branch
needed more than 3 real subsets to be meaningful, so that case (and the
`V_expected<=0 -> None` case) use directly-constructed `SubsetResult`s,
same posture as #77/#85's fabricated-data tests.

255/255 tests green (248 prior + 7 new).

---

## 2026-08-13 -- optimiser/explain.py (Part E SS E.12)

### 91. Marginal bands / Next-Best-Spend is NOT rebuilt here -- already
satisfied by Phase 3's `POST /next-best-spend`

Re-read SS E.12's fourth bullet before writing anything: "evaluator-only
endpoint... for each held card and each of Delta in {1k,10k,50k}, exact
delta-value" is a verbatim description of `app/main.py`'s existing
`/next-best-spend` (`docs/DECISIONS.md`'s own Phase 3 status block already
records it as done, annual marginal-delta MVP). Building a second,
optimiser-side version would be exactly the duplicate-implementation
CLAUDE.md rule 1 exists to prevent. `explain.py`'s docstring points at it
instead of re-deriving it.

### 92. `repair.py`'s `_pooled_spend_per_instance` promoted to
`pooled_spend_per_instance` (public) -- second consumer, same pattern as
`caps.py`'s `window_instances`/`window_flags` (#15)

`threshold_funding_report` needs EXACTLY the pooling logic `repair()`
already runs when hunting for near-miss thresholds -- the only difference
is reporting every breakpoint's status, not just the ones close enough to
top up. Renamed (no behaviour change, `tests/test_repair.py`'s 4 tests
pass unchanged) rather than duplicated, same "a later module importing an
earlier one's shared vocabulary" direction #15 already established.

### 93. Threshold funding analysis stops at thresholds; cap-binding state
is a real, separately-logged gap, not silently skipped

SS38 asks for two things: "which caps were hit (binding segment)" and
"which thresholds were funded vs left short and by how much."
`threshold_funding_report` only builds the second half. The first needs
Stage 5's per-window cap_state (bound vs unbound) surfaced somewhere --
`caps.py`'s `apply_caps` computes this internally (which segment of a
capped rule's chain actually got filled) but doesn't return it on
`AccrualResult` or anywhere else, the exact same fidelity gap #27 already
named for the trace schema generally. Compiling ONLY threshold
breakpoints here (never caps) also matches `repair.py`'s own established
boundary (#71: cap breakpoints are already optimal by construction in
`allocate.py`'s LP, so there was never a reason to compile them for the
near-miss search either).

### 94. Crossover scans always re-solve via `allocate()` + `repair()` --
SS E.12's "evaluator only, no MILP" is read as describing the single-card
case, not a literal constraint on this module

SS38's crossover example ("vary one driver... re-evaluate the top-2
portfolios (evaluator only -- no MILP)") doesn't specify how a MULTI-card
portfolio's spend should be re-priced as one category's spend changes
without re-running its allocation. Freezing the old allocation's card-by-
card split and just re-pricing each card's frozen share through the
evaluator would silently understate value the moment a card's segments
saturate differently at the new spend level (e.g. a capped rule's
overflow boundary shifts) -- exactly the kind of smoothed number
`optimiser/candidates.py`'s own #77 entry already rejected for standalone
value ("no card is ever excluded on smoothed numbers"). `scan_driver`
therefore always calls `allocate()` + `repair()` at every grid point,
which happens to collapse to a pure `evaluate_card`-only call whenever
`portfolio_a`/`portfolio_b` are single-card lists (the LP has nothing to
decide with one card and c0) -- i.e. it satisfies SS E.12's own worked
example exactly, while staying correct for the general multi-card case
the example doesn't cover.

### 95. Marginal-value-curve kinks: a monthly-window cap's breakpoint must
be multiplied by its window's instance count before comparing against an
ANNUAL spend grid -- caught before it shipped a silently wrong number

This was the one place this slice almost got wrong. `Breakpoint.
threshold_spend` is spend-domain (docs/DECISIONS.md #30) but scoped to ONE
window instance -- syn_ecom's `cap_ecom` breakpoint is `Rs20,000`, meaning
"Rs20,000 in one calendar month," not "Rs20,000 a year." A first draft of
`marginal_value_curve` compared this directly against the ANNUAL spend
grid (`_spend_with_driver`'s `annual_amount`), which would have placed the
cap's kink marker at Rs20,000 -- nowhere near the curve's actual slope
change, which the hand-computed test fixture proves happens at
Rs2,40,000 (Rs20,000/mo * 12, exactly where `test_marginal_value_curve_
hand_computed_points_and_kinks`'s Rs2,16,000/Rs2,40,000/Rs2,64,000 points
show the reward growth rate switching from 5% to 1%). Fixed by
multiplying every breakpoint's `threshold_spend` by
`len(engine.caps.window_instances(bp.window))` before the range check --
12 for `calendar_month`, 1 for `anniversary_year`, matching the
"uniform-seasonality instances share the annual total equally" model
`_spend_with_driver` itself implies (it never touches `seasonality`, only
`annual_amount`). Explicitly does NOT apply when the swept line carries a
custom seasonality (`_annualised_kinks` returns `()` in that case,
verified by `test_marginal_value_curve_skips_kinks_for_a_custom_
seasonality_line`) -- the uniform-split assumption breaks down and a wrong
kink marker is worse than no marker; the curve's own points are computed
by a real `evaluate_card` call regardless and are correct either way.

### 96. No new assumption-registry defaults this slice

Unlike frontier.py (#82) and scenarios.py (#90), nothing in `explain.py`
introduces a numeric default needing sign-off -- grids/spans are always
caller-supplied (there's no spec-stated "scan from X to Y" number to
transcribe), and the ledger/threshold-report/curve functions are pure
reshaping of numbers the engine already produces.

### Verification

`tests/test_explain.py` (10 tests). `build_card_ledger` checked against a
plain `evaluate_card` call (Rs960.00 reward / Rs0 milestones / Rs0
benefits / -Rs590.00 costs = Rs370.00, matching test_repair.py's own
near-miss baseline hand computation exactly). `threshold_funding_report`
run against test_repair.py's three already-hand-verified `AllocationResult`
fixtures directly, asserting the near-miss (Rs4,000 short, within buffer),
genuine-shortfall (Rs28,000 short, outside buffer), and comfortably-funded
(-Rs3,80,000 gap, i.e. Rs3,80,000 of headroom) cases without re-deriving
any of their numbers. `scan_driver` verified three ways on syn_ecom-vs-
syn_flat's exactly-linear rate structure in the overflow regime: a
crossover landing precisely on a grid point (Rs19,20,000, cross-checked
against 3 full hand-computed grid points), the same crossover recovered
by interpolation when the grid skips it, and a range with no crossover at
all (syn_ecom's uncapped 5% always beats syn_flat's 1.5%).
`find_smallest_flip` verified over two drivers sharing one zero-baseline
spend input (proving the "other" line's placeholder value doesn't leak
into either scan) -- the flipping driver sorts first with the correct
`change_needed`, the non-flipping one's `None` sorts last.
`marginal_value_curve` verified against 5 hand-computed points spanning
syn_ecom's waiver AND cap breakpoints in one sweep, with `#95`'s
annualisation fix confirmed to place both kinks (Rs1,00,000 and
Rs2,40,000) exactly where the point-by-point numbers show the curve
actually bending -- plus a custom-seasonality case proving kinks are
omitted, never mislabelled, when the annualisation assumption doesn't hold.

265/265 tests green (255 prior + 10 new).

---

## 2026-08-13 -- POST /optimise (Part E SS E.0/E.1), Phase 4 complete

### 97. `CardRepository` gained `get_all_card_bundles()` -- SS E.2's "live
card universe" input didn't have a source yet

`get_card_bundle(key)` (one card) and `get_currencies()` were the only
two methods either repository implementation had -- nothing returned "the
whole catalog," which SS E.2 names as candidate selection's actual input
("live card universe (from `current_card_versions`)"). Added to the
`CardRepository` Protocol and both implementations:
`SyntheticCatalogRepository` maps `bundle_from_dict` over all of
`seeds/synthetic_cards.py`'s `CARDS`; `PostgresCardRepository` adds one
new query (`_fetch_all_card_keys`, joining `cards` to
`current_card_versions`) and reuses the existing per-card `_fetch_card_
dict` for each key (N+1 queries -- the simplest correct option at
today's catalog size; a single wide query is a performance follow-up
noted here, not a correctness concern raised now).

### 98. Discovered empirically, before it could ship as a crash: candidate
selection over the FULL live catalog needs a compatibility pre-filter, or
one unsupported card takes down the entire optimisation

Before wiring `/optimise`, ran every one of the 12 synthetic cards
through a plain `allocate([bundle], ...)` + `repair(...)` call (exactly
what `optimiser/candidates.py`'s own `_standalone_value` does for every
universe card, unconditionally, with no exception handling anywhere in
that loop). Result: **3 of 12 cards raise**, for two genuinely different
reasons that needed distinguishing before deciding what to do about them:

- **Genuine `allocate.py` scope gaps, no request can work around them**
  (docs/DECISIONS.md #68/#70): `syn_points` (`cap_portal` is
  `rule_group`-scoped; only `scope="rule"` reward caps are supported) and
  `syn_slab` (incremental `tier_mode`, needs fill-order binaries `allocate.
  py` doesn't have).
- **Missing request configuration, not a code gap**: `syn_lounge`'s
  countable `dom_lounge` benefit needs `benefit_need`/`benefit_unit_value`
  assumptions (`engine.evaluate.evaluate_card` raises without them,
  correctly, per its own existing behaviour) -- supplying those two
  assumptions in the request makes `syn_lounge` probe-compatible, verified
  directly. (`syn_miles`/`syn_travel`/`syn_renewal` looked like they'd
  fail too on a first pass with NO `primary_routes` declared for
  `synth_points`'s 4-route currency -- but that's the same "missing
  configuration, not a gap" story, and they all probe-compatible once
  `primary_routes={"synth_points": "stmt"}` is supplied.)

Without a filter, `select_candidates` would let whichever ONE of these
three cards happens to be in the universe crash candidate selection for
the entire catalog -- every OTHER card's opportunity lost because of one
incompatible one, exactly the "one bad card sours everything" failure
mode `/optimise`'s whole purpose (finding the BEST card(s)) can't afford.
Fixed with `app/main.py::_partition_universe`: a pre-flight
`allocate`+`repair` probe per universe card (the request's actual
spend/assumptions, so it catches BOTH failure classes above in one
mechanism), splitting the universe into `compatible` (fed to
`select_candidates` as before, untouched) and `excluded` (reported in the
response as `{card_key, reason}` pairs, SS E.2's own "why was card X even
considered / not considered" transparency principle applied one step
earlier than SS E.2 itself describes -- before ranking, not after).
`optimiser/candidates.py`, `optimiser/allocate.py`, and `optimiser/
repair.py` themselves are UNCHANGED -- they keep raising exactly as
before for any direct caller (including a hand-picked `candidate_
universe` that still names an incompatible card); this filter lives
entirely at the API orchestration layer, not inside the optimiser
package. Costs one extra `allocate`+`repair` solve per compatible card
(duplicate of what `_standalone_value` does next) -- immaterial at
today's <=20-card catalog scale, not worth caching away this pass.

### 99. `candidate_universe` request field: explicit override, not just
"pull everything"

`OptimiseRequest.candidate_universe: list[str] | None = None` -- `None`
pulls the full live catalog via `get_all_card_bundles()` (the SS E.2
default); a caller can instead pin an exact key list. Two reasons beyond
convenience: (1) it's how this endpoint's own test suite stays fast and
deterministic without depending on which of the live catalog's cards
happen to be `allocate()`-compatible on a given day (docs/DECISIONS.md
#98) or what today's default assumptions are; (2) it's the natural seam
wallet mode will need later (#10/#61) -- "candidate universe = full
catalog + my currently-open cards" is a straightforward extension of the
same parameter, not a new one.

### 100. Response scope: frontier + classification + robustness only --
explain.py's surfaces are NOT wired into this endpoint

Part E SS E.1's ASSEMBLE step lists "frontier, ICV table,
classifications, size recommendation, explanations." The first four map
directly onto `frontier.build_frontier` + `classify.classify_portfolio`'s
existing outputs, assembled here one-to-one. "Explanations" (`optimiser/
explain.py`'s why-this-card ledger, threshold funding report, crossover
scans, marginal value curves) is deliberately NOT bundled into
`OptimiseResponse` -- every one of those functions needs its own
per-query input (which two portfolios to compare for a crossover, which
category to sweep for a marginal-value curve) that a single "give me the
optimal portfolio" response has no natural way to supply generically for
every possible question a user might ask. These stay available as
already-tested library functions for future dedicated endpoints (an
`/explain/*` family), not force-fit into this one's shape.

### 101. No persistence -- consistent with Phase 3's own deferral, not a
new gap

SS E.1's own output line ("written to `optimisation_runs` +
`portfolio_subset_results` + `evaluation_runs`") isn't implemented --
`/optimise` computes and returns everything in one request/response
cycle, same posture Phase 3's `/evaluate` already established
("evaluation_runs/evaluation_traces persistence: not yet done"). No new
decision needed here, just confirming the pattern holds.

### 102. Frontier's T1/T2 constants and scenarios.py's Low/High factors
stay at their module defaults -- not exposed as per-request overrides

`OptimiseRequest` exposes candidate-selection tuning (`standalone_n`,
`champion_*`, `max_total_candidates`) and classification tuning
(`icv_meaningful`, `strategic_feature_cards`) as request fields, but NOT
frontier.py's `abs_floor`/`rel_pct`/`fee_cover_ratio`/`fee_de_minimis`
(#82) or scenarios.py's `low_factor`/`high_factor` (#90). Those five are
still flagged FOR Satya's sign-off, not yet signed off -- exposing them as
ad hoc per-request knobs before that sign-off happens would let a caller
silently override a registry value nobody has actually approved as
editable yet. Easy to add once C.7's registry has a real home for them;
not done speculatively now.

### Verification

`tests/test_api_optimise.py` (6 tests), FastAPI `TestClient` against
`SyntheticCatalogRepository` (same pattern as `test_api_evaluate.py`).
End-to-end run on `{syn_ecom, syn_flat}` at Rs12,00,000/yr ecommerce
reproduces test_frontier.py's and test_scenarios.py's own independently
hand-verified numbers exactly through the full HTTP stack (frontier
points Rs21,600.00/Rs26,400.00, DeltaV=Rs4,800.00, ICVs Rs8,400.00/
Rs4,800.00 both KEEP, robustness `v_low`=Rs22,800.00) -- proof the
orchestration wiring introduces no drift from the already-verified
per-module numbers. `n_tol=1` caps the recommendation via the API exactly
as `test_frontier.py`'s own unit test does. `run_scenarios=false` is
confirmed to leave `robustness=null` and `t3_pass=null` (not a default
pass/fail). The `_partition_universe` filter is checked two ways: `syn_
slab` alongside a compatible card excludes cleanly (recommendation still
succeeds on the survivor) and two incompatible cards together correctly
return 422 rather than a confusing empty-success response. A manual
smoke run against the FULL live catalog (all 12 cards, `candidate_
universe` omitted) confirmed the predicted exclusion set (`syn_points`,
`syn_slab`, `syn_lounge`) and produced a sane end-to-end recommendation
with no unhandled exception -- the scenario none of the pinned-universe
unit tests exercise directly.

271/271 tests green (265 prior + 6 new). **Phase 4 complete.**

---

## 2026-08-13 -- docs/Part_I_Ingestion_Workflow.md drafted (Phase 5 kickoff)

### 103. Part I didn't exist -- stopped before writing any Phase 5 code,
per CLAUDE.md's own instruction

Asked to "start Phase 5," first action was opening `docs/` for Part I
(CLAUDE.md: "read this file fully before doing anything," "open and
follow that section"). It wasn't there -- only Parts A/B, C, D, E exist.
Confirmed this wasn't a search miss: grepped the whole `docs/` tree for
"Part I"/"ingestion"/"sources," found only forward-references FROM other
parts (Part C SS C.9: "live card data enters only through Part I's
verified-source workflow"; Part D Decision 4's `source_links` schema,
built to support a workflow that was never itself specified). This is
categorically different from Phase 6's frontend gap, which CLAUDE.md
already marks explicitly ("Part F, to be authored") -- Part I was
referenced as if it already existed. Asked Satya rather than improvising
an ingestion architecture unilaterally (AskUserQuestion, three options:
draft Part I first / Satya supplies an existing doc / skip straight to a
minimal concrete step). **Satya chose: draft Part I first**, reviewed
before any `compute/` code follows -- exactly CLAUDE.md's "STOP and ask,
log the decision, don't silently pick an interpretation" posture, applied
one level up (to a whole missing spec section, not just an ambiguous
field).

### 104. Part I's bundle format extends the IMPLEMENTED card-dict shape,
not Part C SS C.2.10's illustrative JSON verbatim

Noticed while drafting SS I.2: Part C SS C.2.10's `CardRuleSet` example
uses `"fees": {"joining": 5000, "annual": 5000, "gst_rate": 0.18}`, but
the actual implemented shape everything in `compute/` consumes
(`seeds/synthetic_cards.py`'s raw dicts, `engine/card_bundle.py::
bundle_from_dict`, `supabase/migrations/0001_init.sql`'s `card_versions`
columns) is flat (`joining_fee`, `annual_fee`, `forex_markup`) and
nested under `"version"` at the dict layer -- a pre-existing, tolerated
drift between Part C's illustrative naming and what the code actually
parses (nothing broke because `bundle_from_dict` was always written
against the real shape, not literally C.2.10's prose). Part I's ingestion
bundle format is specified against the REAL shape (so a drafted bundle is
mechanically close to what `ingest link` will insert, per SS I.9), with a
note flagging the C.2.10 naming drift explicitly rather than silently
reproducing it as if it were consistent.

### 105. Source citation granularity is per-THRESHOLD, not per-tier --
read directly off `source_links.entity_type`'s CHECK constraint, not
assumed

While designing SS I.2's bundle format, checked what `entity_type` values
`source_links` actually allows (`0001_init.sql` line ~303-305):
`card_version, earning_rule, threshold, cap, exclusion, benefit,
surcharge, redemption_route, reward_currency` -- no `threshold_tier`.
A citation can only attach at the threshold level, never to one specific
tier within a multi-tier threshold. Documented as a real schema
constraint in SS I.2 ("Granularity note, read directly off the schema,
not assumed") rather than glossed over -- a card whose ₹4L and ₹8L
milestone tiers come from two different source pages still cites both on
the ONE threshold object's `source_refs` list; the schema has no finer
grain than that, and Part I's tooling spec (SS I.9) shouldn't pretend
otherwise.

### 106. Golden coverage extended to real cards as an explicit PUBLISH
precondition -- a real, load-bearing decision, not a restatement

Part C SS C.11 states the golden-battery requirement for the 12 SYNTHETIC
structural examples specifically ("the golden test battery of SS55 plus
one golden scenario per example card above"). Part I SS I.8 extends this
principle to every REAL card_version as a hard PUBLISH gate (SS I.4/I.9):
no card_version reaches `published` without >=1 hand-computed golden
scenario verified against `engine.evaluate.evaluate_card`, same
discipline as every `compute/goldens/golden_syn_*.json`. Justification
recorded in SS I.8 itself: this is the only mechanism in the whole
pipeline that catches a WRONG TRANSLATION of a correctly-cited source
(e.g. a selector matching the wrong category) -- structural linting (SS
I.4 LINT) checks the JSON is well-formed and cited, never that it means
what the source actually said. `docs/DECISIONS.md` #7 (the real
`caps.py` index-shift bug a golden caught, that no schema validation
would have) is the concrete precedent cited for why this matters, not
just spec-completeness for its own sake.

### 107. Reviewer approval is explicitly a human-only action -- Claude
(or any automated drafting step) may never self-approve a source_link

SS I.0 and SS I.5 both state this directly, not as boilerplate: an AI
assistant drafting an ingestion bundle is doing real, useful work
(capturing sources, transcribing rule text, flagging ambiguity), but
`source_links.reviewer_status = 'approved'` requires a human
independently re-checking the cited source -- self-certification by
whatever produced the draft isn't review, it's just restating the draft
with more confidence. This is CLAUDE.md rule 4 ("NEVER add real card
reward data from memory") taken to its logical conclusion: the risk
rule 4 guards against isn't just Claude typing in a remembered number, 
it's Claude (or any automation) being trusted to mark its OWN transcription
verified. The pipeline (SS I.4) makes this structural -- REVIEW is a
distinct stage after LINK, not a flag any drafting tool can set.

### Scope, deliberately not decided here

- **No scraping/automation architecture specified.** SS I.9 specifies the
  tooling's INPUT/OUTPUT contract (`ingest lint/link/review-queue/
  publish`, what each reads and writes) but not how sources get found or
  fetched (headless browser? manual download? which libraries) --
  Satya's call when Phase 5 code actually starts, not an ingestion-workflow
  concern.
- **No re-verification cadence specified.** SS I.7 states the
  mechanism (`last_checked_at`, a changed source is a new source per SS
  I.6) but deliberately leaves HOW OFTEN sources get rechecked as an
  operational decision, not an engine-level rule.
- **No actual real card was ingested.** SS I.10's worked example uses an
  explicitly fictional "Example Bank Ultra" card with invented numbers,
  flagged as illustrative-only in its own opening line -- CLAUDE.md rule
  4 applies to this document's own worked example too, not just to future
  code.

**Status: awaiting Satya's review of `docs/Part_I_Ingestion_Workflow.md`
in full. No `compute/` code for Phase 5 until it's approved, per his own
instruction when this was scoped.**

---

## 2026-08-15 -- First real-card ingestion validation: CASHBACK SBI Card

Satya supplied a hand-drafted ingestion bundle and golden ahead of Part
I's own tooling (SS I.9, not built yet) specifically to pipeline-test the
engine against real terms. Per his own instructions, worked through in
order and stopped at the first genuine problem rather than routing around
it. `docs/Part_C_Rules_Engine_and_JSON_Schema.md` and `docs/
Part_D_Database_Architecture.md` re-read in full against every field in
`compute/ingestion/bundle_sbi_cashback.json`, cross-checked against the
IMPLEMENTED loader (`engine/card_bundle.py::bundle_from_dict`) and the
real dataclasses (`engine.costs.Surcharge`, `engine.eligibility.
ExclusionSelector`), not just Part C's prose.

### 108. Six pure field-name mismatches between the bundle's dialect and
the implemented loader -- exactly the C.2.10-vs-implemented-shape drift
Part I decision #104 predicted, now observed for real

`card_key`/`key`, `card_name`/`name`, `fees`/`version`,
`threshold_rules`/`thresholds`, a tier's `threshold`/`threshold_amount`,
a surcharge's `surcharge_rate`/`rate`. The `threshold_rules`/`thresholds`
one is the sharpest: as literally written, `bundle_from_dict` would have
**silently dropped the entire fee-waiver threshold** (`card.get(
"thresholds", [])` returns `[]`, no error at all) -- precisely the
"silently drop a field" failure mode Satya's own step 1 instruction
named. Translated by a new adapter, `_adapt_ingestion_bundle`, in `tests/
test_golden_sbi_cashback.py` -- **the ingestion bundle file itself was
never edited**; it's Satya's hand-drafted, source-annotated artifact
(MITC + e-kit T&C), reviewed as evidence, translation kept entirely on
the test side and documented inline.

### 109. `currency` block needed restructuring, not renaming

The bundle nests `{key, routes}` inside the card object; the loader
expects `card["currency"]` to be a bare string, with currency+routes
declared separately (`currencies_from_dicts` on a standalone list,
mirroring `seeds/synthetic_cards.py`'s own `CURRENCIES`). As written,
`bundle.currency_key` would have bound to the whole dict, and the first
`currencies[bundle.currency_key]` lookup anywhere in the engine would
raise `TypeError: unhashable type: 'dict'`. Fixed by feeding
`currencies_from_dicts([raw["currency"]])` as a separate call rather than
threading it through `_adapt_ingestion_bundle` at all -- the bundle's own
`{key, routes}` shape already matches what `currencies_from_dicts`
expects per-entry, it just needed to be pulled out of the card object.

### 110. Surcharge waiver sub-object has no home in the schema at all --
confirmed against `engine.costs.Surcharge`'s actual fields, not assumed

`surcharges[0].waiver` (1% fuel-surcharge refund, txns Rs500-3,000, capped
Rs100/statement) has no corresponding field anywhere -- `Surcharge` is
exactly `key, selector, rate, gst_on_surcharge`. Per the already-settled
`syn_fuel` precedent (#26: "surcharge waivers are just capped
negative-cost rules"), this needs to become a SEPARATE capped
`earning_rule` refunding the surcharge, not a sub-object on the surcharge
itself -- the bundle doesn't have one yet. Dropped for this run (`_adapt_
ingestion_bundle` keeps only `rate`/`gst_on_surcharge`/`selector`,
discards `waiver`) -- confirmed inert for both golden scenarios (neither
touches fuel spend), but this bundle does NOT yet model the card's real
fuel-surcharge economics, and won't until that earning_rule is authored.

### 111. Both of the bundle's exclusions use selector fields that are not
just "unsupported" but actively dangerous if passed through naively --
confirmed by reading `eligibility.py` directly, not inferred

`cashback_mcc_exclusions` (`mcc_include`) and `min_txn_100` (`txn_max`)
both use selector fields `engine.card_bundle._exclusion_selector_from_
dict` silently drops without raising (it only ever reads `categories`/
`channels`/`merchant_groups`/`geography` from a raw dict -- confirmed by
reading the function body, not assumed from the earlier-logged "still
open" item in the table above). The resulting `ExclusionSelector` would
have every field `None`; `engine.eligibility._selector_matches` treats an
all-`None` selector as **matches everything** (no early-return-False
branch ever fires) -- since both exclusions are `excluded_from:
["rewards"]`, passing either through as literally written would have
**zeroed out ALL cashback**, not just fuel/rent/sub-Rs100 spend. This is
sharper than the long-standing "mcc_include/txn_max still rejected" open
item implied -- for earning-RULE selectors an unsupported field would at
least raise loudly in some paths; for THIS specific adapter
(`_exclusion_selector_from_dict`), it fails silently and produces the
most dangerous possible wrong answer (over-broad exclusion) rather than
an inert one. Both exclusions omitted entirely for this run (confirmed
inert for both scenarios: neither touches an MCC-excluded category, and
`min_txn_100` is txn-mode-only precision by the bundle's own admission,
inexpressible in category mode regardless of engine support) -- flagged
loudly in the test file's own docstring, not silently absorbed.

### 112. Scenario A skipped, not worked around -- EMI exclusion has no
representation anywhere in the schema, confirmed with Satya

Scenario A (SBI's own PDF-published worked example, expected Rs1,350)
subtracts EMI-converted spend before applying the 5% online rate. Checked
every place "EMI transaction" could plausibly live: Part C's Selector
(no EMI dimension in its field list), category-mode `CategorySpend`/
`SpendSegment` (no EMI field), the bundle's own `exclusions` (neither of
its two exclusions is EMI-related). The only `is_emi` field anywhere in
the whole schema is `user_transactions.is_emi` (migration line ~409) --
transaction-mode-only, not wired into `evaluate_card` at all. Presented
two honest paths to Satya (pre-filter the test's own input to already-
eligible spend, vs. treat as a genuine schema gap and defer) rather than
picking one silently. **Satya chose: defer.** `test_scenario_a_pdf_
worked_example` is `@pytest.mark.skip`ped with the full reasoning as its
skip message -- a permanent, visible record in the suite itself, not just
in this log -- rather than silently omitted or worked around by
restructuring what the golden's own input represents. EMI/transaction-
flag selector support is now a real, named future task (Part C SS C.1
principle 1: "a versioned engine extension, never an ad-hoc special
case"), not a gap papered over to get one golden green.

### Verification

`tests/test_golden_sbi_cashback.py`: `test_ingestion_bundle_loads_
without_crashing` confirms the translation itself is complete (every
KeyError/TypeError above resolved). `test_scenario_b_steady_state_annual`
verified two ways that agree exactly: stage-by-stage (matching `tests/
test_goldens.py`'s own pattern) gives the online/offline/cap breakdown
directly -- online capped to Rs24,000.00/yr (Rs2,000/mo x 12, `cap_
online_monthly` BINDS every month since Rs50,000/mo x 5% = Rs2,500 >
Rs2,000), offline uncapped at Rs2,400.00/yr (Rs200/mo x 12, well under
both its own and the aggregate cap) -- and the full `evaluate_card()`
orchestrator independently reproduces the identical gross reward, waiver
achievement, fee, and both NACV figures (Rs26,400.00 steady-state,
Rs25,221.18 Year-1) against Satya's own hand computation exactly, arbiter-
by-arithmetic as instructed. Scenario A skipped per #112. 273/273 tests
green (271 prior + 2 new) + 1 skipped.

**This card_version was never published, never inserted into Postgres --
this run only validates the engine against a hand-drafted bundle in
memory.** Per Satya's own step 5: five items remain in the bundle's own
`_review_checklist` needing human source-verification, plus the newly-
found gaps above (EMI selector, MCC/txn-value selectors, fuel-surcharge-
waiver remodelling) before this card could even be RE-DRAFTED completely,
let alone pass Part I SS I.4's LINK/REVIEW/PUBLISH stages.

---

## 2026-08-15 -- Renamed bundle_sbi_cashback.json / golden_sbi_cashback.json
in place to match the loader

### 113. The files themselves were edited this time -- a deliberate
reversal of the earlier "adapter only, never touch the source" posture,
on Satya's explicit instruction

Entry #108 kept every translation on the test side specifically because
the bundle was, at that point, Satya's freshly-hand-drafted artifact
under review -- editing it felt like overwriting evidence mid-review.
Satya then asked directly for the files to be renamed to match the
loader. Read as: the naming review is DONE (he's seen the findings, the
translation is understood and correct), so the translation layer's
proper home is now the artifact itself, not a shadow adapter a future
reader would have to discover. Applied:

- `bundle_sbi_cashback.json`: `card_key`->`key`, `card_name`->`name`,
  `fees`->`version`, `threshold_rules`->`thresholds`, a tier's
  `threshold`->`threshold_amount`, a surcharge's `surcharge_rate`->
  `rate`, and `currency` restructured from a card-embedded `{key,
  routes}` object into a bare string (`"currency": "cashback_inr"`) plus
  a separate top-level `"currencies": [...]` list -- mirrors `seeds/
  synthetic_cards.py`'s own standalone `CURRENCIES`, not a new pattern.
- `golden_sbi_cashback.json`: Scenario B's `spend_annual` keys renamed to
  the golden-battery's own `"category[/channel]"` convention (`tests/
  test_goldens.py::_parse_spend_annual`) -- `"online"`/`"offline"` became
  `"ecommerce/online"`/`"offline_retail/pos"` (category choice arbitrary
  within the earning rules' channel-only selectors; picked for zero
  ticket-size floor loss, noted inline in the golden itself now).
  `fee_paid_steady_state`->`fee_paid`, matching every synthetic golden's
  own field name.

### 114. What was deliberately NOT renamed, and why -- restated directly
in the bundle file itself this time, not just in this log

`exclusions[]` and the fuel surcharge's `waiver` sub-object were left
alone. Renaming implies "this was misspelled"; neither is -- `mcc_include`
IS Part C's own correct Selector field name, and `waiver` describes a
real mechanism the schema simply has no object for yet. Fixing the NAME
wouldn't fix the underlying gap (no engine support; no schema field),
and pretending it would (e.g. by renaming `exclusions` to something a
loader wouldn't accidentally pick up) would trade one silent failure mode
for another. Instead, `bundle_sbi_cashback.json` now carries a permanent
`_engine_compatibility_note` field stating exactly what #111 already
established (loading `exclusions[]` as-is is unsafe, not just inert) --
the warning now lives in the artifact a future ingestion tool would
actually read, not only in this log and a test-file comment.

### Verification

`_adapt_ingestion_bundle` in `tests/test_golden_sbi_cashback.py` shrank
from a multi-field translator to a single `{**raw, "exclusions": []}`
override -- confirms the rename closed every gap it was meant to close,
leaving only the one gap (#111) that renaming can't close by
construction. Re-ran the full suite after both file edits: same 273/273
+ 1 skipped, same numbers in `test_scenario_b_steady_state_annual`
(online Rs24,000.00, offline Rs2,400.00, steady-state Rs26,400.00,
Year-1 Rs25,221.18) -- the rename moved names, not values, exactly as
intended.

---

## 2026-08-15 -- Reviewed the bundle's _review_checklist against both primary sources

### 115. Fetched and read both full source PDFs rather than answering the
checklist from search snippets or partial extraction

`WebFetch` on both PDF URLs returned unparseable binary/encoded content
the first time (a real, worth-recording gap: the tool's HTML->markdown
path doesn't handle these particular PDFs) -- the fallback that worked
was reading the tool's own saved local copy directly with the Read
tool's native PDF support, which returned full per-page text for both
the 47pp e-kit T&C and the 57pp MITC. Read both in full before answering
anything, not just grepped for the 5 checklist phrases -- this is what
surfaced the Rs.99 "Rewards Redemption Fee" question (#117) that
wouldn't have come up from a targeted search.

### 116. Checklist items 1 and 3 confirmed by direct quote; item 2 resolved
by absence of a stated exclusion, not by a direct quote -- the
distinction is recorded, not glossed over

Item 1 (online/offline channel mapping) and item 3 (3.5% forex, no
premium-tier exception) both have a sentence in the source that answers
the question directly -- recorded verbatim in the bundle's new
`_review_findings` block. Item 2 (does rent count toward the Rs.2L fee-
waiver) does NOT have a sentence that says so -- the MITC's waiver
clause ("Waived off on annual spends of Rs.2 Lakh or more") simply
never attaches an exclusion, while the reward-side MCC exclusions live
in a separately-scoped section of a DIFFERENT document (the T&C, not
the MITC) that explicitly frames itself as reward-only ("Cashback shall
not be earned for..."). The bundle's original `_note` reading is
supported by this absence, not proven by a positive statement -- recorded
as "resolved in the bundle's favour, not airtight" rather than upgraded
to "confirmed," so Satya's review pass knows exactly how much weight
this finding can bear.

### 117. New open question surfaced beyond the original 5-item checklist,
added to it rather than silently resolved either way

MITC p.31's "Rewards Redemption Fee: Rs.99 ... Applicable only on
Physical products, Statement Credit & on Vouchers..." names "Statement
Credit" as a fee-bearing redemption type -- CASHBACK SBI Card's own
payout mechanism IS a statement credit. But `reward_terms` SS11.1(a)
describes that credit as automatic ("directly credited... within two
working days of statement generation"), with no customer action
described anywhere as a "redemption." Genuinely ambiguous from the text
which reading is correct (does the Rs.99 fee apply to CASHBACK's
automatic posting, or only to a different, points-based card's active
redeem-to-statement-credit flow). Per Part I SS I.3's discipline
("ambiguous wording is flagged, not resolved by best guess"), added as
a 6th `_review_checklist` item rather than either assuming it applies
(understating NACV by nothing, i.e. treating it as inapplicable without
evidence) or modelling a Rs.99 cost with no textual basis for when it
would fire.

### 118. Findings written into the bundle file itself (`_review_findings`),
not left only in this log or in chat -- but `reviewer_status` on both
`_sources` entries stays `unreviewed`

Same posture as #114's `_engine_compatibility_note`: a durable artifact
beats a conversation that scrolls away. `_review_findings` carries the
exact quotes, page/section citations, and verdicts for every checklist
item, plus the three additional cross-checks done opportunistically
(MCC list exact match, cap amounts/scope/overflow all confirmed, fuel
surcharge waiver corroborated by BOTH sources independently). What was
explicitly NOT done: touching either source's `reviewer_status` field.
Per Part I SS I.0/I.5 (drafted, not yet signed off, but treated as
binding regardless): gathering and presenting evidence against a primary
source is useful, legitimate work an AI assistant can do; marking that
evidence "approved" is a distinct act reserved for a human who
independently re-checks it. The two are kept structurally separate in
the bundle file itself, not just in this log's prose -- `_review_
findings` is additive documentation, `_sources.*.reviewer_status` is
the actual gate, and only the second one requires Satya.

### Verification

`bundle_sbi_cashback.json` re-validated as well-formed JSON after the
edit (`python -c "import json; json.load(...)"`); `tests/test_golden_
sbi_cashback.py` re-run to confirm the new `_review_checklist`/`_review_
findings` fields (both ignored by `bundle_from_dict`, same as `_note`/
`_source` always have been) changed nothing: 2 passed, 1 skipped,
identical to before this pass. No `compute/` code touched this
session -- purely an evidence-gathering and documentation pass, per the
user's own framing ("review the sources checklist").

---

## 2026-08-15 -- First per-entity reviewer approval recorded, exposing a
bundle-format granularity gap

### 119. Recorded Satya's approval on the entity it applies to, not on the
source it cites -- source-level `reviewer_status` can't represent
partial approval when one source backs multiple facts

Satya's instruction was specific: approve the rent-inclusion reading
(checklist item 2), while the Rs.99 Rewards Redemption Fee question
(also citing `mitc`) stays open. The bundle's `_sources.mitc.
reviewer_status` field, as built, is ONE flag for the WHOLE source --
flipping it to `"approved"` would have silently swept the still-open
Rs.99 question in with it, which is exactly the kind of overclaim Part
I SS I.5 exists to prevent ("approved" must mean a human actually
checked THAT fact, not "some fact this source supports"). Fixed by
adding `_reviewer_status: "approved"` and `_reviewer_note` (recording
who approved it, when, and why) directly on `thresholds[0]` (the
fee_waiver threshold entity) instead -- the fact that was actually
reviewed. `_sources.mitc.reviewer_status` is left `unreviewed`,
correctly, since the source as a whole still has an open question
attached to it.

### 120. This is the real schema's own granularity, not a new invention --
`source_links` is per `(source_id, entity_type, entity_id)`, never per
bare source

Checked `0001_init.sql` before deciding how to fix this (lines ~300-319):
`source_links` has no "whole source approved" concept at all -- every
row is a specific (source, entity) pair, each with its OWN `confidence`/
`reviewer_status`. One MITC source with a hundred cited facts would, in
the real schema, produce a hundred independent `source_links` rows, some
approved and some not, simultaneously. The bundle file's `_sources.*.
reviewer_status` was a drafting-time simplification that happened to
work as long as every entity citing a source shared one approval state
-- this task is the first time that assumption broke (two facts, two
different states), and the fix (per-entity `_reviewer_status`) brings
the FILE format one step closer to matching the DB format it's meant to
produce, rather than patching around the mismatch. Not extended to every
other entity in the bundle speculatively -- only `thresholds[0]` needed
it this pass; the others still read their approval state from
`_sources.*.reviewer_status` until one of them also needs to diverge
from its source's overall state.

### Verification

`bundle_sbi_cashback.json` re-validated as well-formed JSON; `tests/
test_golden_sbi_cashback.py` re-run (2 passed, 1 skipped, unchanged --
`_reviewer_status`/`_reviewer_note` are new keys `bundle_from_dict`
never reads, same as `_note`/`_source` always have been). `_review_
checklist` item 2 marked `[APPROVED by Satya 2026-08-15]` inline, with
a pointer to where the actual approval record lives; the Rs.99 item's
wording changed from "surfaced during findings pass" to "STILL OPEN,
pending confirmation" so a future reader scanning the checklist sees
at a glance which items are settled and which aren't, without needing
to cross-reference `_review_findings` for status.

---

## 2026-08-15 -- Investigated the Rs.99 Rewards Redemption Fee question

### 121. Three converging points resolve this by inference, not by a
single sentence that says "CASHBACK is exempt" -- recorded with that
honesty, not overstated as CONFIRMED

Asked to "confirm the Rs.99 question," re-read both already-cached
source documents specifically looking for anything connecting to it,
rather than re-answering from the first pass's surface-level read. Found:

1. The MITC's own fee entry doesn't claim universal applicability -- it
   says "as specified in the individual product Terms & Conditions,"
   explicitly pointing at `reward_terms` (CASHBACK's own T&C) to settle
   the question rather than answering it itself.
2. `reward_terms` Sec 11 is unusually thorough about cashback mechanics
   -- forfeiture events (SS11.6), timing (SS11.1(e)), the aggregate cap
   (SS11.1(j)), the sub-Rs.100 exclusion (SS11.1(s)), even rounding
   (SS11.1(v)) -- and never once mentions a redemption fee. A document
   this granular about every OTHER cost-bearing detail of the program is
   the kind of place a Rs.99 fee would appear if it applied.
3. `reward_terms` FAQ 14 directly answers "what do I have to do to
   receive the earned Cashback" with "The Card Cashback will be
   automatically credited... " -- no cardholder action. A REDEMPTION fee
   presupposes an active redemption choice (SBI's points-based cards:
   converting points into one of several catalog options -- merchandise,
   voucher, or statement credit); CASHBACK has no catalog and no choice
   to attach a fee to. The one place Sec 11 uses "redeemed" at all
   (SS11.5(f), the voluntary-closure edge case) carries no fee either.

None of these is a direct "CASHBACK is exempt from the Rs.99 fee"
sentence -- this is an absence-plus-context inference, same evidentiary
category as the rent-inclusion finding (#116). Recorded in `_review_
findings.checklist_item_6_rs99_redemption_fee` as `PROPOSED READING:
DOES NOT APPLY -- pending Satya's confirmation, not yet approved` --
deliberately not upgraded to CONFIRMED or marked approved by Claude,
matching Part I SS I.0/I.5's human-only approval rule exactly as
applied to #119/#120's rent-inclusion approval. The `_review_checklist`
entry's inline tag was updated to match (`[PROPOSED READING: DOES NOT
APPLY -- pending Satya's confirmation, ...]`), same "make the checklist
itself legible at a glance" pattern #118 established.

### Verification

`bundle_sbi_cashback.json` re-validated as well-formed JSON; `tests/
test_golden_sbi_cashback.py` re-run (2 passed, 1 skipped, unchanged --
new `_review_findings` content, no field `bundle_from_dict` reads).
No `compute/` code touched -- this remains a source-investigation and
documentation task, no change to the engine or the golden numbers.

---

## 2026-08-15 -- Rs.99 finding approved; approval recorded on the
finding itself, not on a bundle entity

### 122. Approving an ABSENCE has nowhere to live but the finding record
-- unlike #119's rent-inclusion approval, there is no field to attach
`_reviewer_status` to

Satya approved the proposed reading from #121 (the Rs.99 fee doesn't
apply to CASHBACK). #119 set the precedent that per-entity approvals
belong on the specific entity they concern, not on the source-level
flag -- but that precedent assumed an entity EXISTS to attach the
approval to (there, `thresholds[0]`). Here, correctly, nothing in the
bundle models this fee at all (the proposed reading was that it doesn't
apply, so there's no `earning_rules`/`caps`/`surcharges` entry for it to
be a fact about). The approval is therefore recorded directly on
`_review_findings.checklist_item_6_rs99_redemption_fee.verdict`
("APPROVED by Satya 2026-08-15: does NOT apply") rather than invented
onto some entity that would then misleadingly look like it exists to
carry a citation. Both `_sources` entries stay `unreviewed` throughout,
same as before -- neither source is "fully reviewed" as a whole, only
specific findings drawn from them are.

### 123. All 6 `_review_checklist` items now resolved -- status snapshot,
not a new decision

For the record: items 1 and 3 confirmed by direct quote (#116); item 2
approved by Satya, recorded per-entity (#119/#120); items 4-5 were never
source questions (pre-accepted engine-modelling notes, #116); item 6
(surfaced during the findings pass itself, not in the original 5)
approved by Satya, recorded on the finding (#122, this entry). This
bundle's checklist is fully worked through -- what remains before this
card_version could even be re-drafted for LINT/LINK/REVIEW/PUBLISH (Part
I SS I.4) is the surcharge-waiver remodelling (#110) and the exclusions
engine-support gap (#111/#114), both already logged as separate,
larger design tasks, not checklist items.

### Verification

`bundle_sbi_cashback.json` re-validated as well-formed JSON; `tests/
test_golden_sbi_cashback.py` re-run (2 passed, 1 skipped, unchanged).
No `compute/` code touched.

---

## 2026-08-15 -- `ingest lint` built (Part I SS I.9's first tool)

### 124. `card_bundle.py`'s selector loaders were silently defeating the
engine's own already-correct validators -- a real bug, found and fixed
at the root, not worked around

While designing the lint tool's "engine compatibility" check, went to
reuse `match.py`'s existing guard against unsupported selector fields
rather than re-implement it. Found `match._validate_rule` (now public,
see #126) already existed, already correctly enumerated every C.2.1
selector field, and already raised on an unsupported one -- but never
actually fired for any ingested bundle, because `card_bundle.py`'s
`_selector_from_dict`/`_exclusion_selector_from_dict` only ever
populated `categories`/`channels`/`merchant_groups`/`geography` on the
dataclass, silently discarding `mcc_include`/`mcc_exclude`/`networks`/
`merchants`/`txn_min`/`txn_max`/`date_from`/`date_to` during dict->
dataclass translation. By the time the validator ran, those fields were
already `None` -- nothing to complain about. This is exactly the
`_exclusion_selector_from_dict` danger already logged as #111
("silently drops mcc_include/txn_max... ExclusionSelector matches
EVERYTHING") -- but #111 framed it as an eligibility.py-specific issue;
building this tool revealed it's a `card_bundle.py` loader bug affecting
earning_rules and surcharges identically, and that the fix belongs in
ONE place (the loader), not three (one workaround per consumer). Fixed
`_selector_kwargs_from_dict` (a new shared helper both `_selector_from_
dict`/`_exclusion_selector_from_dict` now call) to populate every C.2.1
field. Zero behaviour change for any existing fixture (no synthetic
card, and no already-reviewed part of `bundle_sbi_cashback.json`'s
`earning_rules`/`surcharges`, ever sets these fields) -- confirmed by
the full suite staying green before writing a single new test.

### 125. Surcharges had no validator AT ALL -- a second, separate gap
from #124, not the same bug wearing a different hat

Checked whether `costs.py` had an equivalent guard before assuming #124
covered it. It didn't -- `surcharge_cost` calls `selector_matches`
directly with no validation step anywhere, for either of the two reasons
`match.py`/`eligibility.py` have one (loader silently dropping fields,
OR no check written at all). This is the "no check written at all" case.
Added `costs.validate_surcharge`, mirroring `match.validate_rule`
exactly, called from `surcharge_cost`'s own loop. Built public from the
start (not private-then-promoted like the other two, #126) since it was
written already knowing the lint tool needs to call it per-item.

### 126. `validate_rule`/`validate_exclusion` promoted from module-private
to public, specifically so the lint tool reports EVERY bad rule/
exclusion in one run, not just the first

First version of the lint tool's engine-compatibility check ran the
bundle through `match()`/`apply_eligibility()` directly and caught
whatever `ValueError` came out. Tested against the real bundle (two bad
exclusions: `cashback_mcc_exclusions` using `mcc_include`, `min_txn_100`
using `txn_max`) and found only ONE reported -- `apply_eligibility`'s
own validation loop raises on the FIRST bad exclusion and stops, never
reaching the second. A drafter using this tool to fix a bundle would
have to fix-and-rerun repeatedly to discover problems one at a time,
exactly the friction a lint tool exists to remove. Fixed by promoting
`match._validate_rule` -> `match.validate_rule` and `eligibility.
_validate_exclusion` -> `eligibility.validate_exclusion` (same rename
pattern as #15/#92/etc.: private helper promoted when a second, real
caller needs it directly) and having the lint tool call each one
PER ITEM, collecting every issue instead of stopping at the first.
Re-ran against the real bundle: both exclusions now correctly reported
in one pass. `match()`/`apply_eligibility()` themselves are unchanged --
they still call the (now-public) functions internally exactly as before.

### 127. `compute/ingest/` bundle loader reconciles Part I's own spec
against how the one real bundle actually turned out -- both spellings
accepted, not a forced fourth edit

Part I SS I.2's worked example specifies `"source_refs": [...]` (a list)
per entity and `"sources": {...}` at the top level. `bundle_sbi_
cashback.json` -- drafted, reviewed, and partly approved across three
prior sessions, entirely before this tool existed -- independently
settled on `"_source": "..."` (singular string, underscore-prefixed,
matching this repo's `_note`/`_engine_compatibility_note` convention)
and `"_sources": {...}`. `ingest.bundle.source_refs`/`declared_sources`
accept both spellings (`_source` treated as a one-element `source_refs`)
rather than forcing a fourth edit of an artifact that's already been
through real human review to match a spec detail that turned out not to
match practice. Part I SS I.2 itself should be updated to document this
as the actual convention -- flagged here, not yet done (a docs-only
follow-up, doesn't block this tool).

### 128. Deliberately does NOT implement Part C SS C.11's original four
structural checks -- confirmed absent from the whole repo before
claiming anything about them, and the report says so out loud

Before writing `lint.py`, searched the entire `compute/` tree for
selector-overlap linting, threshold-payload depth checking, cap-scope
resolution, and currency/route completeness -- none exist anywhere,
confirmed by search, not assumed (Part C's own SS C.11 prose is the only
place these are described). Building all four from scratch was out of
scope for "start the tooling" -- each is a real, separate design task
(e.g. selector-overlap linting needs to reason about priority+specificity
ties across an entire rule set, not validate one object at a time the
way this pass's checks do). `LintReport.checks_not_implemented` states
this explicitly, and the CLI prints it on every run, unconditionally --
a bundle passing `ingest lint` is NOT the same claim as "passes Part C
SS C.11," and the tool says so rather than implying broader coverage
than it has.

### 129. Running the finished tool against the real bundle found something
new -- the currency/route carry no source citation at all

Neither `currencies[0]` (`cashback_inr`) nor its one route
(`statement_credit`) has ever carried a `_source`/`source_refs` --
missed across every one of this bundle's three prior manual review
passes (items 1-6 of `_review_checklist`). Added as item 7, with an
open question rather than a silent fix: is a 1:1 statement-credit ratio
(cashback denominated directly in rupees, `v=1` by construction) the
kind of fact Part I SS I.0 actually means to require sourcing for, or
is "this currency literally IS rupees" self-evident enough not to need
one the way a transfer ratio would? Either answer is defensible; picking
one silently would be exactly the guess SS I.0 exists to prevent. This
finding is itself the argument for building the tool at all -- a human
(or Claude) reading the bundle top-to-bottom can miss an entity; a
per-entity mechanical check structurally cannot.

### Verification

`compute/ingest/` (new package: `bundle.py`, `lint.py`, `cli.py`,
`__main__.py`) plus `tests/test_ingest_lint.py` (16 tests) and `tests/
test_card_bundle.py` (6 tests, up from 5 -- the surcharge validator
regression test added this pass). Lint tool tested two ways: fabricated
minimal bundles isolate one behaviour at a time (missing citation,
undeclared source key, bad selector field, translation failure, the
"reports ALL bad exclusions not just the first" fix), and the real
`bundle_sbi_cashback.json` is run through the whole tool end-to-end,
asserting the EXACT 4 findings (2 provenance, 2 engine-compatibility) --
a lock-in test, not just a smoke test: if this tool ever disagrees with
what prior manual review found by hand, that's a signal worth
investigating, not noise to relax the assertion around. CLI smoke-tested
directly (`python -m ingest lint ingestion/bundle_sbi_cashback.json`,
exit code 1, all 4 issues printed with the not-implemented list) before
writing the formal `capsys`-based CLI tests. Full suite: 294/294 green
+ 1 skipped (278 prior + 16 lint tests, replacing the 5 test_card_
bundle.py tests with 6 after the surcharge regression test was added).

Only `ingest lint` is built. `ingest link`/`ingest review-queue`/
`ingest publish` (Part I SS I.9's remaining three) are not registered
as CLI subcommands at all yet -- deliberately, not stubbed with a fake
"not implemented" message, since that would look like partial coverage
of something that doesn't exist. Next slice, whenever it comes: `ingest
link` (writes `sources`/card rows/`source_links` to Postgres, `status=
'draft'`) -- the first `compute/` code in this repo that touches the
catalog tables via anything other than `seeds/seed.py`'s synthetic
fixtures.
