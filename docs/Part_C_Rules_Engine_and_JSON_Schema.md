# Credit Card Portfolio Optimiser — Part C
## Deterministic Rules Engine and JSON Schema

Version 0.1 · Incorporates the breakpoint-preservation safeguard and resolutions to open questions 1–4.

---

# C.0 The breakpoint safeguard — where the risk actually lives, and the design that closes it

Your concern is correct in principle, but it is worth locating precisely where discontinuities can leak, because most of them **cannot** leak in the Part B architecture, and knowing which ones can makes the fix cheap.

**What is already exact inside the inner MILP (never smoothed):**

- Milestones, fee waivers, and rate-unlock thresholds are **binary variables with exact thresholds** (constraints B.4-(4)/(5)). If routing ₹30,000 more to a card unlocks a ₹10,000 voucher, the inner MILP sees the full ₹10,000, not an averaged rate.
- Monthly / quarterly / annual cap boundaries are **exact segment widths**. The kink at a cap is in the model, at the right rupee value.
- Retroactive tier transitions are binaries (B.5).

**What is genuinely approximated, and can therefore leak:**

1. **Candidate pre-filtering.** A card scored by its smoothed standalone value may be dropped *before* enumeration even though it becomes optimal once its milestone is considered. This is the real version of your "inferior under average effective rate" scenario.
2. **Threshold-edge fragility.** The optimiser, using effective rates ê, may park eligible spend at *exactly* a threshold T. The exact evaluator, applying floor rounding and true eligibility, may find eligible spend a few hundred rupees *below* T — milestone silently lost, value cliff.
3. **Floor rounding itself** — small, continuous in aggregate, no breakpoints of its own.

The design therefore adds two targeted mechanisms instead of a general neighbourhood search — same guarantee, far less compute:

### Safeguard 1 — Breakpoint-aware candidate scoring (fixes leak 1)

Pre-filtering (B.7) already takes per-category champions. Add a third champion metric: for every candidate card, compute a **milestone-adjusted best-case score**:

```
MABC(c) = max over each threshold tier τ of c:
          NACV(c) evaluated with milestone-eligible spend forced to exactly T(τ),
          drawing that spend from the user's categories in eR∩eM order,
          capped by the user's actual total spend
```

Any card in the top-N by MABC enters the candidate set even if its average-rate standalone value is mediocre. MABC is a handful of evaluator calls per card (one per tier) — trivial cost, and it is computed by the *exact* evaluator, so no smoothing is involved in the inclusion decision. Combined with the existing per-category champions, the enumeration can no longer be blind to a card whose entire case is a discontinuity.

### Safeguard 2 — Compiled breakpoints + threshold repair pass (fixes leak 2)

Every threshold and cap in the schema below is a **first-class object with an ID** (this is a deliberate schema decision, not an accident). At solve time the engine compiles, per enumerated portfolio, the full **breakpoint list**: every (card, threshold, window) tuple with its exact rupee value.

After the exact evaluation of the optimiser's proposal (pipeline step 4 → 5 of your seven-step flow):

```
for each breakpoint β in the portfolio:
    m = exact milestone/waiver-eligible spend near β (from the evaluator trace)
    if 0 < T(β) − m ≤ buffer(β):          # near-miss just below
        build allocation A⁺: top-up the cheapest-opportunity-cost spend to cross T(β)
        exact-evaluate A⁺
    if 0 < m − T(β) ≤ buffer(β) and z(β)=1: # barely-made, verify robustness
        confirm evaluator agrees the threshold is met; if not, evaluate both
        the topped-up and the withdrawn variant
select the highest exact-value allocation among original + all variants
```

`buffer(β)` defaults to `max(₹5,000, 2% · T(β))` — covering rounding drift and eligibility-mask differences. The number of breakpoints per portfolio is small (typically 10–30), each variant is one evaluator call, so the repair pass costs tens of milliseconds. This is your step 5–7, but **scoped to the enumerated breakpoint list** rather than a blind perturbation search — it provably covers every discontinuity the schema knows about, which is every discontinuity that exists, because the schema is the sole source of rules.

Additionally, inside the MILP itself, active thresholds are given a small margin — when `z(β)=1`, require eligible spend `≥ T(β) + buffer(β)` unless total spend makes that infeasible — so proposals arrive threshold-robust in the first place, and the repair pass is a backstop rather than a workhorse.

**Net result:** the deterministic evaluator is not merely validating; the exact evaluator participates in candidate generation (MABC), the MILP carries every discontinuity as an exact binary, and the repair pass exhaustively probes the only fragile zone (threshold edges). Nothing economically material is smoothed away anywhere in the flow.

---

# C.1 Rules engine design principles

1. **Closed vocabulary, open data.** The engine understands a fixed set of constructs (selectors, accruals, caps, thresholds, exclusions, benefits). Every Indian card encountered so far in structure is expressible as data in this vocabulary; new cards add rows, not code. If a card genuinely cannot be expressed, that is a versioned engine extension, never an ad-hoc special case.
2. **One construct for every "spend ≥ X ⇒ something happens".** Reward milestones, fee waivers, accelerated-rate unlocks, lounge qualification gates, and renewal benefits are all the same mathematical object with different payloads. The schema unifies them as the **Threshold rule** (C.3). This directly implements your typed-milestone requirement while keeping the evaluator small and uniformly testable.
3. **Determinism is structural.** Explicit priorities, defined tie-breaks, ordered pipeline stages, immutable published versions, and a complete explanation trace. Same inputs + same rule versions ⇒ byte-identical output.
4. **Channels, not fake categories.** UPI, online, POS, contactless, international are dimensions of a transaction, orthogonal to its economic category (your resolution to Q2). A grocery purchase over UPI is grocery spend that also matches UPI-channel rules.
5. **Assumptions live in a registry, not in rules.** Ticket sizes, point-value estimates, lounge values, utilisation defaults are configuration with a defined override order (your resolution to Q3).

---

# C.2 Core object model

All objects carry `id`, `version`, `effective_from`, `effective_to`, `status` (`draft | published | deprecated`), and `source_refs[]` (per §4 of the master prompt). Published objects are immutable; changes create a new version. Fields below are the semantic payload.

## C.2.1 Selector — "which spend does this rule touch?"

```json
{
  "categories":      ["grocery", "ecommerce"],      // null = all
  "merchants":       ["bigbasket"],                  // normalized merchant keys
  "merchant_groups": ["quick_commerce"],
  "mcc_include":     [5411, 5499],
  "mcc_exclude":     [6540],
  "channels":        ["upi", "online", "pos", "contactless"],   // null = all
  "networks":        ["rupay"],                      // usually implied by card
  "geography":       "domestic",                     // domestic | international | all
  "txn_min":         100,                            // ₹, transaction mode only
  "txn_max":         null,
  "date_from":       null,                           // for seasonal/dated rules
  "date_to":         null
}
```

Match semantics: a spend segment matches if it satisfies **every** non-null field (AND across fields, OR within a list). In category mode, `channels`/`mcc` matching uses the category→MCC/channel mapping in the assumptions registry with an `estimated` trace flag.

## C.2.2 Accrual — "how are rewards computed on matched spend?"

```json
{ "type": "per_unit",  "unit_amount": 150, "points_per_unit": 5,
  "rounding": "floor_per_txn", "currency": "hdfc_rp" }

{ "type": "percentage", "rate": 0.05,
  "rounding": "floor_paise_per_txn", "currency": "cashback_inr" }
```

`rounding ∈ { floor_per_txn, round_per_txn, floor_on_aggregate, none }`. Cashback is just a currency with `v ≡ 1`. Category-level evaluation applies the ticket-size approximation (A.2) and stamps `rounding_estimated: true` in the trace when the rounding effect exceeds the materiality threshold (C.7).

## C.2.3 Cap

```json
{ "id": "cap_ecom_monthly",
  "measure": "reward",             // reward | spend
  "amount": 1000,                  // in currency units (points or ₹)
  "window": { "kind": "calendar_month" },
  "scope": "rule_group:ecom_accel",   // rule | rule_group:<key> | card
  "overflow": "base_rate"          // base_rate | zero
}
```

`scope` lets several accelerated rules share one cap (common: "10X capped at 15,000 points/month across all Smartbuy-type categories"). `rule_group` keys are declared on earning rules. Every cap ID appears in the compiled breakpoint list (C.0, Safeguard 2).

## C.2.4 Window (reset clocks)

```json
{ "kind": "calendar_month" }
{ "kind": "quarter", "alignment": "calendar" }        // or "anniversary"
{ "kind": "calendar_year" }
{ "kind": "anniversary_year" }
{ "kind": "statement_cycle" }                          // MVP: evaluated as calendar_month,
                                                       // trace flag "cycle_approximated"
```

The evaluator owns a **clock resolver** that maps every window to concrete month-bucket sets for the modelling year. Anniversary alignment uses the user's `card_anniversary_month` when known (wallet mode), else defaults to modelling-year alignment with a trace flag — this upgrades the A.17 simplification from silent to visible.

## C.2.5 Exclusion — with independent scopes (Q1 resolution)

```json
{ "id": "excl_rent",
  "selector": { "categories": ["rent"] },
  "excluded_from": ["rewards"],            // any of: rewards | milestones | fee_waiver
  "note": "Rent earns no points but DOES count toward milestone and waiver spend"
}
```

This is the formal home of the `reward_eligible_spend` vs `milestone_eligible_spend` distinction: the evaluator materialises three eligible-spend views per card by applying exclusions per scope. A category can be in any of the 8 combinations.

## C.2.6 EarningRule

```json
{ "id": "er_ecom_5pct",
  "card_version": "cardX_v3",
  "selector": { "...": "..." },
  "accrual": { "...": "..." },
  "caps": ["cap_ecom_monthly"],
  "rule_group": "ecom_accel",
  "priority": 100,
  "stacks_with_base": false        // true: adds on top of base rule; false: replaces it
}
```

**Conflict resolution (deterministic, in order):** higher `priority` wins → more specific selector wins (specificity = count of non-null selector dimensions) → if still tied, publication order (earlier rule wins) and a validation *warning* is raised at publish time. `stacks_with_base` covers "5X = base 1X + bonus 4X" structures where the bonus has its own cap but base continues uncapped — a very common Indian pattern that naïve schemas get wrong.

## C.2.7 Threshold rule — the unified construct (full detail in C.3)

## C.2.8 Benefit

```json
{ "id": "ben_dom_lounge",
  "kind": "countable",                    // countable | flat_perk | voucher
  "unit_label": "domestic lounge visit",
  "entitlement": 8,
  "entitlement_window": { "kind": "calendar_year" },
  "qualification": "th_lounge_q",         // optional Threshold ID gating the quota
  "value_ref": "assump.lounge_domestic_value",   // registry pointer, not a hardcoded ₹
  "utilisation_ref": "user.lounge_need",
  "guest_charge": 0,
  "supplementary_included": false
}
```

Countable benefits feed portfolio deduplication (A.9). `voucher` kind carries `face_value`, `expiry_days`, `friction_ref`.

## C.2.9 RewardCurrency and RedemptionRoute — as specified in A.7; routes carry `ratio`, `friction_ref`, `min_points`, `per_point_fee`, `transfer_partner`.

## C.2.10 CardRuleSet — the versioned bundle

```json
{ "card_version": "cardX_v3",
  "card_id": "cardX",
  "effective_from": "2026-04-01",
  "effective_to": null,
  "fees": { "joining": 5000, "annual": 5000, "gst_rate": 0.18 },
  "forex_markup": 0.035,
  "earning_rules": ["..."],
  "thresholds": ["..."],
  "exclusions": ["..."],
  "benefits": ["..."],
  "currency": "cardX_points",
  "surcharges": [ { "selector": {"categories":["government"]}, "rate": 0.01, "gst_on_surcharge": 0.18 } ]
}
```

Devaluation (§68) = publish `cardX_v4` with new `effective_from`; historical versions remain queryable for before/after analysis. **Never overwrite.**

---

# C.3 The Threshold construct — typed milestones done once

One schema object covers your entire required typology:

```json
{ "id": "th_annual_voucher",
  "card_version": "cardX_v3",
  "basis": {
    "measure": "milestone_eligible_spend",     // or waiver_eligible_spend
    "selector_override": null,                 // optional narrower selector
    "window": { "kind": "anniversary_year" }
  },
  "tier_mode": "cumulative",                   // cumulative | highest_only
  "tiers": [
    { "threshold": 400000, "payload": { "type": "grant_voucher",
        "benefit": "ben_taj_voucher_10k" } },
    { "threshold": 800000, "payload": { "type": "grant_voucher",
        "benefit": "ben_taj_voucher_10k" } }
  ]
}
```

**Payload types** (your list, mapped):

| Requested milestone type | Payload |
|---|---|
| spend-triggered reward / bonus points | `grant_points { amount, currency }` |
| cashback milestone | `grant_cashback { amount }` |
| voucher / airline voucher / hotel voucher | `grant_voucher { benefit_id }` (typing lives on the Benefit; vouchers inherit utilisation u and friction φ per A.5) |
| fee-waiver milestone | `waive_fee { fee: "annual" }` |
| accelerated earning-rate threshold | `activate_rule { rule_id, application: "prospective" \| "retroactive" }` |
| lounge-access qualification | `grant_entitlement { benefit_id, quantity, window }` |
| renewal-benefit milestone | `grant_voucher` / `grant_points` with `basis.window = anniversary_year` and `condition: "on_renewal"` |

**Tier semantics:** `cumulative` = every crossed tier pays; `highest_only` = only the top achieved tier pays (evaluator suppresses lower payloads). Both required by real Indian cards.

**Clocks:** any Window from C.2.4, including quarter and statement-cycle bases (statement-cycle approximated per the clock resolver, always trace-flagged).

Because fee waivers are Threshold rules, the "waiver" machinery in Part A/B is just the payload `waive_fee` — one code path, one test suite, one breakpoint compiler. Every tier of every Threshold automatically lands in the breakpoint list.

---

# C.4 Evaluation architecture — the deterministic pipeline

Input: `(user_profile, spend_input, portfolio, allocation x, rule_versions, assumptions_snapshot)`.
Output: `(valuation, explanation_trace)`.

```
Stage 0  SNAPSHOT      Freeze rule versions + assumptions registry into the run record.
Stage 1  NORMALISE     Expand spend to the (category × channel × month) grid
                       (or transaction list). Apply seasonality. Resolve UPI aggregate
                       input (C.4.1). Attach ticket-size assumptions.
Stage 2  ELIGIBILITY   Apply exclusions per scope → three eligible-spend views
                       (reward / milestone / waiver) per card.
Stage 3  MATCH         Bind earning rules to spend segments via selectors,
                       resolving conflicts per C.2.6. Emit per-segment rule bindings.
Stage 4  ACCRUE        Compute gross points per binding with exact rounding
                       (or ticket-size approximation + estimation flag).
Stage 5  CAP           Apply caps in nesting order txn → month → quarter → year,
                       honouring scope groups and overflow behaviour.
Stage 6  THRESHOLDS-P1 Evaluate months in chronological order; fire prospective
                       payloads (rate unlocks apply from crossing month onward;
                       grants recorded).
Stage 7  THRESHOLDS-P2 Second pass for retroactive payloads (retroactive rate
                       boosts, highest-only tier resolution, annual waivers).
                       Exactly two passes — the dependency graph
                       spend → thresholds → activated rules → rewards has depth 1
                       by schema construction (activated rules cannot themselves
                       contain thresholds), so two passes reach the fixed point.
                       This is a validated schema invariant, not a hope.
Stage 8  VALUE         Convert currencies to ₹ via the user's route (v_exp),
                       apply friction, redemption fees; attach conservative/optimised
                       range values.
Stage 9  BENEFITS      Card-level utilisation, then portfolio-level deduplication
                       (shared Need budgets, A.9).
Stage 10 COSTS         Fees + GST net of waiver payloads; surcharges + GST; forex.
Stage 11 ASSEMBLE      NACV per card, PV, Year-1 and Steady-State variants,
                       3-year cumulative (C.4.2), full explanation trace.
```

## C.4.1 UPI handling (Q2 resolution, operationalised)

Schema: channel-based, exactly as you specified. Onboarding: if the user enters an aggregate "Monthly UPI spend", Stage 1 decomposes it across underlying categories using the registry's `upi_category_mix` default (grocery-heavy; editable), stamps every derived segment `channel: upi, decomposition: assumed`, and the trace discloses the assumption. If the user later supplies category-level UPI detail or transactions, the decomposition is replaced, schema untouched.

## C.4.2 Three-year cumulative value (your added requirement)

The evaluator is year-parametric: `evaluate(year_index ∈ {1,2,3})` where year 1 includes joining fee + welcome payloads, years 2–3 are renewal years (renewal thresholds and renewal benefits active). 

```
V_3yr = V_year1 + V_steady_year2 + V_steady_year3
```

MVP computes years 2 and 3 as identical steady-state runs (no spend growth modelling); the signature supports per-year spend vectors later. Portfolio selection remains steady-state-driven; Year-1 and 3-year figures are displayed. A future toggle can allow 3-year-driven selection where joining economics differ materially — architecture supports it now, product decision deferred.

---

# C.5 Reset-period handling — summary of guarantees

Every cap and threshold names its Window explicitly; nothing is ever annualised inside the engine (§40). The clock resolver is the single component that touches calendars. Uneven seasonality therefore interacts correctly with monthly caps by construction: a ₹1,500 monthly cap yields Σ_t min(monthly reward, 1500), never 18,000, unless spending is genuinely uniform.

# C.6 Rounding — summary of guarantees

Exact floor/round semantics per accrual in transaction mode; ticket-size approximation in category mode with per-rule materiality check: if `|exact-at-assumed-tickets − unrounded| > 1%` of the rule's reward, the result carries `rounding_estimated` and the UI shows "assumes avg. transaction of ₹X — edit". Where the impact is < 1%, no flag — avoiding false precision per your instruction.

# C.7 Assumptions registry (Q3 resolution)

Single versioned configuration document; every assumption referenced by key from rules/valuation, never inlined. Override precedence (highest wins):

```
transaction-derived statistics  >  user override  >  registry default
```

Proposed initial ticket-size defaults (all editable, shown as assumptions):

| Category | Default avg. ticket | | Category | Default avg. ticket |
|---|---|---|---|---|
| Grocery / quick-commerce | ₹700 | | Domestic flights | ₹6,500 |
| Dining / food delivery | ₹600 | | International flights | ₹35,000 |
| Ecommerce (general) | ₹1,800 | | Hotels (domestic, per booking) | ₹9,000 |
| Offline retail | ₹1,500 | | Fuel | ₹1,500 |
| Utilities / mobile / internet | ₹1,200 | | Insurance | ₹20,000 |
| UPI (small-ticket) | ₹350 | | Rent | ₹30,000 |
| Entertainment / movies | ₹800 | | Education / school fees | ₹40,000 |

The registry also holds: point-value route estimates, friction defaults, lounge/movie unit values, `upi_category_mix`, category→MCC/channel maps, materiality thresholds, `buffer(β)` parameters. Every result stores the registry snapshot ID — reproducibility includes assumptions.

# C.8 λ / complexity handling (Q4 resolution, engine-side)

Confirmed and implemented as follows: `λ` remains an internal parameter of the objective, **default 0**. "Exactly N" / "Up to N" journeys run with λ = 0 (cardinality already expresses the user's intent). "Let the optimiser decide" runs the frontier sweep at λ = 0 and hands the raw 1–5-card values to a **transparent recommendation rule** — to be specified in Part E as promised, considering incremental ₹, incremental %, added fees, and stated tolerance, with its reasoning rendered in the UI ("a 4th card adds ₹2,100/yr for ₹2,500 extra fees — not recommended"). No invisible penalty ever moves a recommendation.

# C.9 Example rules — 12 structurally distinct synthetic cards

**Important:** these are *structural* examples for engine testing, deliberately synthetic. They resemble real Indian card patterns but carry no claim of current accuracy; live card data enters only through Part I's verified-source workflow (§5: "do not rely on memory for current reward structures").

**Example 1 — Flat uncapped cashback.** `accrual: percentage 1.5%, floor_paise_per_txn`, no caps, no thresholds. Baseline sanity card.

**Example 2 — Capped accelerated ecommerce cashback.**
```json
{ "earning_rules": [
    { "id": "e2_base", "selector": {}, "accrual": {"type":"percentage","rate":0.01}, "priority": 10 },
    { "id": "e2_ecom", "selector": {"categories":["ecommerce"],"channels":["online"]},
      "accrual": {"type":"percentage","rate":0.05}, "priority": 100,
      "caps": [{"measure":"reward","amount":1000,"window":{"kind":"calendar_month"},
                "scope":"rule","overflow":"base_rate"}], "stacks_with_base": false } ] }
```
Tests: cap binding (§55 test 1), overflow to base.

**Example 3 — Points card with shared accelerated cap.** Base `5 pts / ₹150 floor_per_txn`; travel-portal rule `25 pts / ₹150` with `rule_group: portal_accel` and group cap `15,000 bonus pts / calendar_month`, `stacks_with_base: true` (bonus 20 pts capped, base 5 pts uncapped). Tests: per-unit rounding (§55 test 2), stacking, group caps.

**Example 4 — Cumulative annual milestone vouchers.** Threshold on `anniversary_year`, `cumulative`, tiers ₹4L → voucher A, ₹8L → voucher B. Tests: §55 test 3, voucher utilisation/friction inheritance.

**Example 5 — Fee waiver.** Threshold basis `waiver_eligible_spend`, calendar-anniversary year, tier ₹3,00,000 → `waive_fee: annual`. Paired exclusion: `rent excluded_from [fee_waiver]`. Tests: §55 test 4, waiver-eligibility divergence.

**Example 6 — Retroactive tier cashback.** Threshold `highest_only`, tiers ₹1L→`activate_rule(rate_2pct, retroactive)`, ₹3L→`activate_rule(rate_3pct, retroactive)`. Tests: Stage-7 retroactive pass, highest-only suppression.

**Example 7 — Incremental slab tiers.** Three earning rules with spend-measure caps forming bands (0–1L @1%, 1–3L @2%, >3L @3%, `tier_mode` data on the rule set marks `incremental`). Tests: convex PWL flagging for the optimiser's fill-order binaries (B.5).

**Example 8 — Zero-forex travel card.** `forex_markup: 0`, intl selector rule `2%`, moderate annual fee, waiver threshold. Tests: forex cost convention — this card should attract international spend without any "savings" bookkeeping.

**Example 9 — RuPay UPI card.** Rule selector `{channels:["upi"]}`, `1 pt / ₹100`, monthly reward cap 500 pts; exclusion: `{channels:["upi"], categories:["fuel","rent"]} excluded_from [rewards]`. Tests: channel-based matching, channel×category exclusion, UPI aggregate decomposition.

**Example 10 — Fuel card with surcharge waiver.** Surcharge entry `1% on fuel` at card level *plus* Threshold-free capped rule modelling the waiver: `selector fuel, accrual percentage 0.01 (surcharge refund), cap ₹250/month` — i.e. surcharge waivers are just capped negative-cost rules. Tests: surcharge + waiver netting, §58 economics.

**Example 11 — Quarterly lounge qualification.** Benefit `4 domestic lounge visits / quarter` with `qualification: th_q_spend` where the Threshold basis is `milestone_eligible_spend, window: quarter (calendar)`, tier ₹75,000 → `grant_entitlement`. Tests: benefit gating, quarterly clocks, portfolio dedup with a gated quota (§55 test 5 extended: entitlement only exists in qualified quarters).

**Example 12 — Renewal-benefit + rate-boost combo.** Anniversary-year Threshold: tier ₹5L → `grant_points 10,000` with `condition: on_renewal`; separate tier ₹1L → `activate_rule(dining_2x, prospective)`. Tests: prospective activation timing (Stage 6 chronological pass), renewal conditioning, Year-1 vs steady-state divergence.

Together these cover every construct: both accrual types, both rounding modes, rule vs group vs card cap scopes, both overflow modes, all payload types, both tier modes, all four clock kinds, scope-differentiated exclusions, channel rules, stacking, surcharges, and forex.

# C.10 Explanation trace

Every rupee in the output is a trace node:

```json
{ "amount": 7800, "kind": "reward",
  "card_version": "cardX_v3", "rule_id": "e3_portal", "cap_state": "unbound",
  "window": "2026-07", "spend_basis": 39000,
  "currency": {"points": 6500, "route": "travel_portal", "v": 1.2, "phi": 0.9},
  "flags": ["rounding_estimated"],
  "source_refs": ["src_cardX_rewards_tnc_2026_04"] }
```

The §37 "Why Card B" panel, the §38 threshold analysis, and the audit requirement of §74 are all renderings of this trace. The breakpoint compiler (C.0) and the marginal-band engine (A.15) read the same trace — one data structure serves optimisation, explanation, and audit.

# C.11 Validation hooks

At publish time every CardRuleSet runs: selector-overlap linting (ambiguous priorities), threshold-payload depth check (the depth-1 invariant of Stage 7), cap-scope resolution, currency/route completeness, and the golden test battery of §55 plus one golden scenario per example card above with hand-computed expected values. A rule set cannot reach `published` with failing goldens — financial correctness before visual polish (§72-H), enforced mechanically.

# C.12 Forward pointers

- **Part D** maps these objects to Postgres: JSONB payloads for selectors/accruals/payloads inside strongly-keyed versioned tables (`card_versions`, `earning_rules`, `thresholds`, `exclusions`, `benefits`, `currencies`, `routes`, `assumption_snapshots`, `evaluation_runs`), with the trace stored per run.
- **Part E** consumes the compiled breakpoint list for the MILP and repair pass, and will specify the transparent portfolio-size recommendation rule (C.8).
- **Greenfield vs Wallet:** engine input carries `owned_cards[]` with per-card user overrides (actual fee, anniversary month, current-year milestone progress for mid-year Next-Best-Spend). Empty array = greenfield. One engine, two starting states, as required.
