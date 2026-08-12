# Decisions log

Per CLAUDE.md: when a spec section is ambiguous, or a construct requires an
engine-level judgment call the spec doesn't pin down, it's logged here
instead of silently picked. New assumption-registry defaults are flagged
here too, for Satya's sign-off.

## Status as of 2026-08-12 (commit 9408f25 + syn_retro/syn_renewal goldens)

Phase 2 complete: all 11 engine stages + `breakpoints.py` implemented,
177/177 tests green. 12/12 synthetic cards have a passing golden -- full
C.9 coverage (syn_ecom, syn_flat, syn_fuel, syn_lounge, syn_miles,
syn_points, syn_renewal, syn_retro, syn_slab, syn_travel, syn_upi,
syn_waiver). No blocking deferrals -- 9 non-blocking items remain open
(table below); none of them gate Phase 2 sign-off or starting Phase 3.

**Genuinely open items** (none blocking today's work; listed so a future
session doesn't have to scan all 54 entries below to find them):

| # | Item | Why it's still open |
|---|---|---|
| #2 | C.7's "UPI small-ticket ₹350" ticket-size row | Never confirmed which reading is correct (category ticket vs. flat ₹350 for UPI-channel segments) |
| #11, #43 | Multi-category pooled cap / incremental-band windows | Raise rather than guess an attribution scheme; no fixture needs one yet |
| #9, #32 | Spend-measure caps: only `scope="rule"` supported | `syn_slab` only needs `rule` scope; `rule_group`/`card`-scoped spend-measure caps untested and unimplemented |
| #12, #39 | Caps × `activate_rule` interaction | Identity-based splice + Stage-5-before-Stage-6/7 ordering wouldn't reconcile if a future rule combines both; no current card does |
| #19 | Flat per-currency `RedemptionFees` (A.12) | Not modelled anywhere; no route in the seed catalog carries one |
| #27 | Explanation trace is a flat line-item list | Not C.10's full per-node schema (`cap_state`, per-currency `v`/`phi`, `source_refs`) — a real fidelity gap, flagged for a follow-up pass if §37/§74 need it soon |
| #29 | `WelcomeValue` has no real fixture | Parameter exists in `assemble_nacv`, always 0 in practice — no card/schema payload type for welcome bonuses |
| #10 | True anniversary alignment | Approximated as calendar-aligned; needs `card_anniversary_month` from wallet mode (not built) |
| — | `mcc_include`/`mcc_exclude`/`networks`/`txn_min`/`txn_max`/`date_from`/`date_to` selector fields | Still rejected everywhere selectors are matched (match.py, eligibility.py) — only categories/channels/merchant_group/geography are supported |

**Confirmed and settled (not open)**: `upi_category_mix` weights (#1),
`activate_rule` prospective/retroactive (#13, resolved #36-40), `syn_slab`
fill-order for `rule`-scope bands (#9, resolved #41-45, #45-update),
geography-aware selectors (#4, resolved #51-54), `PV` = NACV for a single
card, not a separate output (#28), `CategorySpend.merchant_group` input
path (#5, resolved #55-56), `condition: "on_renewal"` year-mode filtering
(#14, resolved #59-60). All 12/12 synthetic cards now have a golden.

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
