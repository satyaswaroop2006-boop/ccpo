# Decisions log

Per CLAUDE.md: when a spec section is ambiguous, or a construct requires an
engine-level judgment call the spec doesn't pin down, it's logged here
instead of silently picked. New assumption-registry defaults are flagged
here too, for Satya's sign-off.

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
