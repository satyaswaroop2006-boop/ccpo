# Credit Card Portfolio Optimiser — Parts A & B
## Financial Model and Portfolio Optimisation Mathematics

Version 0.1 · Design specification · Precedes any code (per master prompt §72, §76)

---

# Part A — Financial Model

## A.0 Conventions and foundational design decisions

Before the formulas, five decisions that shape everything downstream. Each is a deliberate choice, and two of them push back on the master prompt.

**Decision 1 — All value is expressed in expected annual rupees, computed for a steady-state year.** Welcome benefits distort card selection: a card with a ₹12,000 welcome voucher looks great in Year 1 and mediocre forever after. The engine therefore computes two numbers for every card and portfolio — **Year-1 Value** (includes joining fee and welcome benefits) and **Steady-State Value** (renewal-year economics only) — and the optimiser selects portfolios on **Steady-State Value by default**. Year-1 value is displayed alongside as information. This prevents the engine from recommending welcome-bonus churn, which is fragile advice and reputationally risky for the product. The master prompt lists welcome benefits inside the main value equation; this spec separates them.

**Decision 2 — Costs, not savings.** The master prompt models "Forex Savings" and "Fuel Benefits" as positive line items. Savings are only defined relative to a baseline ("saved compared to what card?"), which introduces a hidden arbitrary parameter. This spec instead models forex markup, fuel surcharge, and transaction surcharges as **explicit costs on every card**. A zero-forex card then wins on international spend because its cost term is zero, not because a fictitious savings number was added. Same optimisation result, no baseline ambiguity, cleaner audits.

**Decision 3 — Monthly resolution internally.** The engine's canonical time grid is 12 monthly buckets per year. Annual user input is expanded to months via a seasonality vector (default: uniform, `1/12` each month). Monthly caps, statement-cycle caps (approximated as monthly for MVP), and quarterly resets (aggregations of 3 buckets) all become exactly representable. Rules with anniversary-year clocks are treated as aligned to the modelling year in MVP — a documented simplification (see A.17).

**Decision 4 — Three independent eligibility masks.** "Excluded from rewards" ≠ "excluded from milestone spend" ≠ "excluded from fee-waiver spend" (master prompt §57 already demands this). Formally, every card `c` carries three indicator functions over categories:

```
eR(c,k) ∈ {0,1}   spend in category k earns rewards on card c
eM(c,k) ∈ {0,1}   spend in category k counts toward milestones on card c
eW(c,k) ∈ {0,1}   spend in category k counts toward fee waiver on card c
```

All three are stored per rule, never inferred from one another.

**Decision 5 — Optimise-then-evaluate.** Exact reward maths contains floor functions and integer rounding, which are non-convex and poison an optimiser. The architecture is therefore two-layer:

1. The **optimiser** works with piecewise-linear *effective rates* (smooth approximations of the true rules).
2. The **evaluator** — the single deterministic engine required by §52 and §74 — recomputes the exact value of whatever allocation the optimiser proposes, using true floor/rounding/reset logic.

**All user-facing numbers come from the evaluator.** The optimiser only proposes. If evaluator value deviates from optimiser value by more than a tolerance (suggest 2%), the allocation is locally repaired (A.16 note, B.10). This is how the "one engine" principle survives contact with non-convex mathematics.

### Notation used throughout

```
c ∈ C        cards (candidate universe)
k ∈ K        spend categories
t ∈ T        months, T = {1,…,12}
b ∈ B        countable benefits (lounge visits, movie tickets, free nights, …)
j ∈ J(c)     milestones of card c

D(k)         user's annual spend in category k (₹)
season(k,t)  fraction of D(k) falling in month t;  Σ_t season(k,t) = 1
D(k,t)       = D(k) · season(k,t)

x(c,k,t)     spend allocated to card c, category k, month t  (the decision)
X(c)         = Σ_{k,t} x(c,k,t)   total annual spend on card c
```

---

## A.1 Eligible spend

For card `c` in month `t`:

```
RewardSpend(c,k,t)  = eR(c,k) · x(c,k,t)
MileSpend(c,t)      = Σ_k eM(c,k) · x(c,k,t)          (per-month)
MileSpendYr(c)      = Σ_t MileSpend(c,t)               (annual clock)
WaiverSpendYr(c)    = Σ_{k,t} eW(c,k) · x(c,k,t)
```

Milestone clocks vary (quarterly vs annual); `MileSpend` is aggregated over whatever window the milestone rule declares (see A.5).

---

## A.2 Earning rules and rounding

### Exact form (transaction level — the evaluator's ground truth)

The canonical earning rule is **units-based**, never a percentage:

```
points(A) = floor(A / U) · r
```

where `A` = transaction amount, `U` = spend unit (e.g. ₹150), `r` = points per unit (e.g. 5). Cashback rules are the special case `U = 1, r = rate`, with paise rounding as the issuer specifies (store `rounding ∈ {floor, round, ceil}` per rule).

### Category-level approximation (MVP, when only category totals are known)

Rounding loss depends on ticket size. With an average ticket `ā(k)` for category `k` (defaults per category, user-editable), the **effective earn rate** in points per rupee is:

```
ê(c,k) = floor(ā(k) / U) · r / ā(k)
```

Example: rule = 5 pts / ₹150, dining with ā = ₹800 → floor(800/150)·5 = 25 pts per transaction → ê = 25/800 = 0.03125 pts/₹, versus the naïve 5/150 = 0.0333. The gap (~6% here) is exactly why §6 forbids premature simplification to "3.33%". The evaluator always uses the exact form when transactions are available; ê is only the optimiser's planning rate and the category-level fallback.

Rupee-valued earn rate (used everywhere below):

```
v̂(c,k) = ê(c,k) · v(cur(c))        where v(·) is point value from A.7
```

---

## A.3 Caps, accelerated rates, and tiers

### Capped accelerated categories

A rule "5% on ecommerce up to ₹1,000 cashback/month, 1% after" is a **concave piecewise-linear reward curve** in monthly eligible spend `S`:

```
Reward(S) = a · min(S, S̄) + b · max(S − S̄, 0)
S̄ = Cap / a                     (spend that exhausts the cap)
```

with `a` = accelerated rupee rate, `b` = overflow rate. Overflow behaviour must be stored per rule: `overflow ∈ {base_rate, zero}` — issuers differ, and assuming `base_rate` when it is `zero` overstates value.

Caps are enforced **per reset window on the monthly grid**: monthly caps directly; statement-cycle caps approximated as monthly (MVP simplification, flagged in A.17); quarterly caps over `t ∈ {1–3, 4–6, 7–9, 10–12}`; annual caps over all 12.

### Tier semantics — a distinction the schema must capture

Slab rules ("₹0–1L = 1%, ₹1–3L = 2%, above = 3%") come in two materially different variants:

- **Incremental**: each rate applies only to spend within its band. Piecewise-linear, *convex* (increasing rates) — needs care in the optimiser (B.5).
- **Retroactive**: the achieved tier's rate applies to *all* spend. Discontinuous jumps — modelled like milestones (binary events).

Store `tier_mode ∈ {incremental, retroactive}` on every tiered rule. Conflating these is one of the most common comparison-site errors.

---

## A.4 Gross reward value

For each card, month, and category, apply rules in this order (matching §16):

```
1. eligibility mask eR
2. exclusions (rule-level merchant/MCC/channel exclusions)
3. earning rule (exact floor form, or ê at planning time)
4. min/max transaction filters (transaction mode only)
5. caps, in nesting order: transaction → monthly → quarterly → annual
```

Annual gross reward value of card c:

```
GR(c) = Σ_t Σ_k CapApply( v̂(c,k) · RewardSpend(c,k,t) )
```

where `CapApply` is the concave curve of A.3 evaluated on the relevant window.

---

## A.5 Milestones

Milestone `j` on card `c`: threshold `T(j)` on milestone-eligible spend within window `win(j)` (month / quarter / year), paying benefit with face value `FV(j)`.

```
z(c,j) = 1[ MileSpend over win(j) ≥ T(j) ]
MilestoneValue(c) = Σ_j z(c,j) · FV(j) · u(j) · φ(j)
```

`u(j)` = user utilisation factor for the milestone's benefit type (a ₹10,000 hotel voucher is not worth ₹10,000 to a user who won't use it — §14 applied to milestones too), and `φ(j)` = redemption friction discount (A.7). Milestones create the step discontinuities that make marginal value spike (A.15) and force binaries into the optimiser (B.4).

---

## A.6 Fees and fee waiver

With GST rate `g = 0.18` on card fees:

```
w(c) = 1[ WaiverSpendYr(c) ≥ W(c) ]                    (waiver achieved)

SteadyFee(c) = F_annual(c) · (1 + g) · (1 − w(c))
Year1Fee(c)  = ( F_join(c) + F_annual(c) · (1 − w(c)) ) · (1 + g)
```

GST is only levied on fees actually charged, so it sits inside the waiver bracket. User-specific overrides (§62) replace `F_annual` entirely: lifetime-free ⇒ `F_annual = 0` and the waiver constraint disappears from that user's model.

---

## A.7 Point valuation pipeline

Every reward currency flows through a five-stage pipeline before it becomes rupees:

```
points → redemption route → route ratio → friction discount → rupees
```

For currency `m` and redemption route `ρ` (cash, voucher, travel portal, transfer partner p):

```
v(m, ρ) = ratio(m, ρ) · φ(m, ρ) − perPointFees(m, ρ)
```

`ratio` = rupees per point on that route (for transfers: `transfer_ratio · estimated_partner_point_value`, both stored per §44). `φ ∈ (0,1]` = friction discount capturing minimum-redemption thresholds, blackout risk, expiry risk, portal markups (§45). Suggested defaults: cash/voucher φ = 1.0, travel portal φ = 0.9, airline/hotel transfer φ = 0.75–0.85 — all user-editable, all displayed as assumptions.

Three scenario values per currency (§8):

```
v_cons(m) = max over {cash, voucher} routes
v_opt(m)  = max over all routes including transfers
v_exp(m)  = value of the user's declared primary route for m
            (MVP: user picks one route per currency; later: preference-weighted blend)
```

**The engine prices rewards at `v_exp` everywhere.** `v_cons` and `v_opt` appear in output as a range ("worth ₹13,000–₹31,000 depending on redemption; we assumed ₹24,000 based on your preferences"). Never silently price at `v_opt`.

---

## A.8 Countable benefits and utilisation (card level)

For countable benefit `b` on card `c` (lounge visits, movie tickets, free nights, vouchers):

```
Entitle(c,b)   quota granted by the card (after quota qualification rules —
               quarterly-spend gates etc., which are milestone-type conditions)
Need(b)        user's annual consumption of benefit type b (asked, not assumed)
V(b)           user-editable rupee value per unit (defaults: domestic lounge ₹800,
               international lounge ₹2,500 — labelled as assumptions per §13)
u(c,b)         utilisation factor ∈ {0, .25, .5, .75, 1} for non-countable perks
```

Card-standalone value of countable benefit:

```
BenefitValue(c,b) = min(Need(b), Entitle(c,b)) · V(b)
```

Non-countable perks (memberships, concierge, insurance) use face value × utilisation factor: `FV(c,b) · u(c,b)`.

## A.9 Portfolio-level benefit deduplication

The user's need is a **shared budget across the portfolio** (§42). Let `l(c,b)` be units of benefit `b` consumed via card `c`:

```
Σ_c l(c,b) ≤ Need(b)          l(c,b) ≤ Entitle(c,b)
PortfolioBenefit(b) = Σ_c l(c,b) · V(b)
```

Value is created by *consumed* units, never by quota. Two cards with 8 lounge visits each and a user who takes 6 → portfolio lounge value = 6 · V, allocated to whichever card's quota the optimiser draws down (allocation matters when quotas have qualification gates). Same structure for movies, free nights, OTT subscriptions.

---

## A.10 Forex economics (cost convention)

For international spend routed to card c:

```
ForexCost(c) = m(c) · (1 + g) · Σ_t x(c, intl, t)
```

`m(c)` = forex markup (0 for zero-forex cards; typical 3.5%). International spend also earns rewards through the normal machinery. Net international economics per card emerge naturally: rewards − ForexCost. No baseline, no "savings" line (Decision 2).

## A.11 Surcharge economics

Per (card, category) surcharge rate `σ(c,k)` (fuel, rent platforms, government payments, wallet loads):

```
SurchargeCost(c) = Σ_{k,t} σ(c,k) · (1 + g_σ(k)) · x(c,k,t)
```

with GST applied where levied on the surcharge. Fuel surcharge waivers set `σ = 0` up to the waiver's own monthly cap — modelled as a capped rule like any other. Because the candidate set always includes the **outside option** `c₀` = "pay directly / debit" (zero rewards, zero fees, zero surcharge), the optimiser will route surcharge-negative categories away from cards automatically. This outside option is essential: without it, the model is forced to put rent and government payments *somewhere* on a card even when every card loses money on them.

---

## A.12 Net Annual Card Value

Steady-state, given an allocation `x`:

```
NACV(c) = GR(c)                          gross reward value (A.4, priced at v_exp)
        + MilestoneValue(c)              (A.5)
        + Σ_b l(c,b) · V(b)              portfolio-deduped countable benefits (A.9)
        + Σ_b FV(c,b) · u(c,b)           non-countable perks
        − SteadyFee(c)                   (A.6)
        − RedemptionFees(c)              flat redemption charges if any
        − ForexCost(c)                   (A.10)
        − SurchargeCost(c)               (A.11)
```

Year-1 variant: replace `SteadyFee` with `Year1Fee` and add `WelcomeValue(c) · u · φ`. Reported, not optimised on (Decision 1).

## A.13 Portfolio value

For portfolio `P ⊆ C` with allocation `x`, complexity penalty `λ` (₹/year per card beyond the first, user-set, default suggestion ₹1,500 per §24):

```
PV(P, x) = Σ_{c∈P} NACV(c | x)  −  λ · max(|P| − 1, 0)
PV*(P)   = max_x PV(P, x)        subject to Σ_c x(c,k,t) = D(k,t)
```

`PV*` — the value of a portfolio under its *own best* allocation — is the object everything else is defined on. The outside option `c₀` is always implicitly in `P` and never counts toward `|P|`, fees, or the complexity penalty.

## A.14 Incremental card value — the flagship metric

```
ICV(c | P)     = PV*(P) − PV*(P ∖ {c})          for a held card (keep/close)
ICV(c⁺ | P)    = PV*(P ∪ {c⁺}) − PV*(P)         for a candidate card (add)
Overlap(c | P) = SAV(c) − ICV(c | P)
```

where `SAV(c) = PV*({c})` is standalone value. The critical subtlety: **both sides are re-optimised**. Removing a card lets its spend flow to the next-best home, so ICV is almost always smaller than the card's apparent contribution inside the portfolio. This re-optimisation is what makes KEEP/CLOSE/ADD honest (§3, §30). Classification thresholds:

```
ADD / KEEP      ICV > threshold_meaningful          (suggest ₹1,000/yr default)
OPTIONAL        0 < ICV ≤ threshold_meaningful
CLOSE / SKIP    ICV ≤ 0
HOLD-FOR-FEATURE  ICV ≤ 0 but card is sole provider of a user-flagged
                  strategic feature (zero-forex, UPI, status) — surfaced, not auto-closed
```

## A.15 Rates, break-even, marginal value

```
Gross Reward Rate(c)      = GR(c) / Σ eR-eligible spend on c
Net Reward Rate(c)        = NACV(c) / X(c)
Portfolio Effective Rate  = PV*(P) / Σ_k D(k)
```

**Break-even spend** is defined along the user's own category mix: scale the spend allocated to card `c` by θ ∈ [0,1] holding proportions fixed; break-even is the smallest θ·X(c) where the card's net contribution ≥ 0. Because waivers and milestones are steps, break-even frequently lands *exactly on a threshold* — report the binding threshold by name ("card turns positive at ₹3,00,000, the fee-waiver threshold"), not just a number.

**Marginal value uses bands, not derivatives.** "Value of the next ₹1" is ill-defined under floor rounding and step rewards; the derivative is 0 almost everywhere with occasional ₹10,000 spikes. Define instead:

```
MV(c, k, Δ) = Evaluator( x with Δ added to x(c,k,·) ) − Evaluator( x )
for Δ ∈ {₹1,000, ₹10,000, ₹50,000}
```

computed by the exact evaluator, so milestone spikes ("next ₹30,000 on Card B returns ≈35%") and cap cliffs ("next ₹10,000 on Card A returns 1%, cap exhausted") emerge automatically. This powers the Next-Best-Spend engine (§26) and the marginal value curve (§39). Opportunity cost (§27) is inherent: comparing `MV(cB,k,Δ)` against `MV(cA,k,Δ)` for the same Δ *is* the opportunity-cost calculation.

## A.16 Where the approximation can bite — and the repair rule

The optimiser's planning rates ê ignore floor rounding and assume the seasonality vector. After solving, the evaluator recomputes exactly. Expected gaps: rounding (~1–6% on point cards depending on ticket sizes), seasonality mismatch if the user's real spending is lumpier than declared. Repair rule for MVP: if `|optimiser − evaluator| / evaluator > 2%`, re-run the milestone/waiver binaries fixed to their evaluator-verified states and re-allocate continuous spend once. That closes essentially all practical gaps without iterating.

## A.17 Documented MVP simplifications

1. Statement-cycle caps and clocks treated as calendar-month.
2. Anniversary-year milestone clocks aligned to the modelling year.
3. Temporary merchant offers excluded from structural value unless the user opts in (§43); `offer_confidence` field gates this.
4. No discounting / time-value of money (§41 explicitly permits this).
5. One primary redemption route per currency (blended preferences later).
6. Devaluation modelling = re-run with new rule version (§68); no probabilistic devaluation risk in MVP.

Each simplification is a stored flag, so relaxing it later is an engine change, not a schema change.

---

# Part B — Portfolio Optimisation Mathematics

## B.1 Problem statement

Choose which cards to hold and how to allocate every rupee of declared spend across them (and the outside option) to maximise steady-state portfolio value, subject to reward-rule structure, cardinality, and user constraints. Two entry states, one engine (§22a): BUILD MY PORTFOLIO fixes no cards; OPTIMISE MY WALLET fixes owned-card set as the starting point and evaluates deviations. Both call the same solver with different `y` fixings.

## B.2 Sets, parameters, decision variables

Sets and parameters as in Part A, plus per (c,k,t) a set of **piecewise segments** `q ∈ Q(c,k)` derived from caps and incremental tiers, each with rupee rate `e(c,q)` and monthly width `cap(c,q,t)`.

Decision variables:

```
y(c)      ∈ {0,1}    card c is held                      (c₀ fixed to 1)
x(c,k,t)  ≥ 0        spend routed to card c, category k, month t
s(c,q,t)  ≥ 0        spend inside segment q               (Σ_q s = eligible x)
z(c,j)    ∈ {0,1}    milestone j achieved
w(c)      ∈ {0,1}    annual fee waived
l(c,b)    ≥ 0        units of countable benefit b consumed via card c
```

## B.3 Objective

```
maximise
    Σ_{c,q,t}  e(c,q) · s(c,q,t)                    reward value (v_exp-priced rates)
  + Σ_{c,j}    FV(j)·u(j)·φ(j) · z(c,j)             milestones
  + Σ_{c,b}    V(b) · l(c,b)                        deduped countable benefits
  + Σ_c        PerkValue(c) · y(c)                  non-countable perks × utilisation
  − Σ_c        F(c)·(1+g) · ( y(c) − w(c) )         fees net of waiver
  − Σ_{c,k,t}  σ(c,k)·(1+g_σ) · x(c,k,t)            surcharges
  − Σ_{c,k∈intl,t}  m(c)·(1+g) · x(c,k,t)           forex cost
  − λ · ( Σ_{c≠c₀} y(c) − 1 )                       complexity penalty
```

All terms linear in the variables — this is a mixed-integer **linear** programme.

## B.4 Constraints

```
(1) Demand:        Σ_c x(c,k,t) = D(k,t)                          ∀ k,t
(2) Linking:       x(c,k,t) ≤ D(k,t) · y(c)                       ∀ c,k,t
(3) Segments:      Σ_q s(c,q,t) = Σ_k eR(c,k)·x(c,k,t) per rule-group;
                   s(c,q,t) ≤ cap(c,q,t)
                   (quarterly/annual caps: Σ over the window ≤ window cap)
(4) Milestones:    Σ_{k,t ∈ win(j)} eM(c,k)·x(c,k,t)  ≥  T(j) · z(c,j)   ∀ c,j
(5) Waiver:        Σ_{k,t} eW(c,k)·x(c,k,t)  ≥  W(c) · w(c);   w(c) ≤ y(c)
(6) Benefit dedup: Σ_c l(c,b) ≤ Need(b);   l(c,b) ≤ Entitle(c,b) · y(c)
(7) Cardinality:   Σ_{c≠c₀} y(c) ≤ N_max        (user complexity tier, §24)
(8) User rules:    y(c)=1 for must-keep; y(c)=0 for refused;
                   Σ_{c∈RuPay} y(c) ≥ 1 etc.;  Σ_c F(c)(y(c)−w(c)) ≤ FeeBudget
```

Note the elegance of (4) and (5): because milestone/waiver terms are *rewards* the solver wants, `spend ≥ T·z` needs no big-M — the solver only sets `z=1` when it has genuinely routed the spend, and always sets it when it can. No numerical-tightness pathology.

## B.5 Where the nonlinearity actually lives

- **Capped/decreasing-rate curves (concave)** — safe as plain segments: a maximising LP fills the higher-rate segment first automatically. No binaries.
- **Milestones, waivers, retroactive tiers (convex steps)** — need the binaries above. Retroactive tiers get one binary per tier with the tier rate applied to total spend via standard disjunction.
- **Incremental tiers with increasing rates (convex PWL)** — the solver would fill the *high* segment first, which is wrong. Requires fill-order binaries: `s(c,q) ≥ cap(c,q) · z_fill(c,q+1)`. Rare in Indian cards but present; schema's `tier_mode` flag triggers this.
- **Floor rounding** — not modelled in the MILP at all; handled by optimise-then-evaluate (A.16, Decision 5).

## B.6 Candidate methods compared, and the recommendation

| Method | Exact? | Explainable? | Frontier & ICV | Assessment |
|---|---|---|---|---|
| One monolithic MILP over all cards | yes | moderate | needs re-solves | Fine, but every ICV/frontier query is another solve; harder to debug |
| **Subset enumeration + inner MILP per subset** | yes (within pre-filter) | **highest** | **free by-product** | **Recommended for MVP** |
| Dynamic programming over spend | approximate | low | no | State space explodes with multi-window caps |
| Greedy + milestone correction | no | high | partial | Good as a warm-start/sanity check only |

**Recommended architecture: enumerate card subsets, solve a small inner MILP for each.**

Why this wins for MVP:

1. **Scale is on our side.** Pre-filter to ~12–15 candidates (B.7). Subsets of size ≤ 5 from 15 candidates = C(15,1)+…+C(15,5) = **4,943 subsets**. Each inner problem has card selection *removed* (the subset is fixed), leaving ~20 categories × 12 months × ~2 segments continuous variables and only 5–15 binaries (milestones + waivers). Modern solvers (HiGHS, CBC via OR-Tools/PuLP) dispatch these in milliseconds; the full sweep runs in seconds and is embarrassingly parallel and cacheable.
2. **The efficient frontier (§23) is a free by-product**: best value at each portfolio size = max over subsets of that size. No extra solves.
3. **Every ICV is a lookup**: for final portfolio P and any card c, `PV*(P∖{c})` is already in the results table. KEEP/CLOSE/ADD, incremental analysis (§30), and What-If Lab (§28) become table queries, not re-optimisations.
4. **Explainability is structural**: every recommendation is "portfolio S scored ₹X, here is its allocation," reproducible by running the evaluator on stored allocations. Nothing hides inside a branch-and-bound tree.
5. **Robustness (§64–65) is cheap**: re-run the sweep on Low/High spend vectors; portfolios ranking highly across all three sweeps score as robust.

The monolithic MILP remains the right tool later, when candidate universes grow past ~25 and enumeration stops being cute. Because the inner problem *is* a MILP already, that migration is a loop-removal, not a rewrite.

## B.7 Pre-filtering with a coverage guarantee

Naïve pre-filtering by standalone value is biased: it drops specialist cards (zero-forex, UPI, fuel) whose standalone value is low but whose incremental value inside a portfolio is high. Pre-filter therefore takes the **union** of:

1. Top N by standalone value `SAV(c)` under the user's spend (suggest N = 8).
2. **Per-category champions**: for each category with material user spend (> ₹25k/yr, say), the top 2 cards by net marginal rate on that category alone.
3. Cards required by user constraints (owned, must-keep, "at least one RuPay").

This guarantees the enumeration can't miss a card whose entire purpose is one category. Typical result: 12–15 candidates.

## B.8 Complexity summary

Inner MILP: ~500–1,500 continuous vars, 5–15 binaries → ms. Full sweep: ≤ ~5,000 inner solves, parallelisable, sub-minute worst case on one machine, cache-keyed by (subset, spend-vector hash, rule versions). Annual-mode shortcut: when seasonality is uniform, monthly caps collapse exactly to annualised segment widths (12 × monthly width), shrinking T from 12 to 1 and cutting variable counts ~90% — use this whenever the user hasn't supplied seasonality.

## B.9 Outputs assembled from the sweep

- **Optimal portfolio** at the user's complexity constraint, with allocation → evaluator → displayed numbers (§36).
- **Efficient frontier** table: best 1/2/3/4/5-card values + incremental value of each additional card (§22).
- **Card Playbook** (§25): read directly off the optimal `x` — the card carrying each category's spend.
- **Marginal-value bands** (A.15) per held card → Next-Best-Spend prompts (§26).
- **Threshold analysis** (§38): binding constraints of the inner MILP (which cap, milestone, or waiver constraint is tight) name the crossover levers directly.
- **Explainability audit** (§37): per card, the objective's term-by-term decomposition *from the evaluator*, stored with rule versions and source references.

## B.10 Integrity rules

1. The evaluator is the sole source of user-facing numbers; the optimiser never is (Decision 5).
2. Steady-state value drives selection; Year-1 shown as information (Decision 1).
3. Every solve stores: candidate set, rule versions, assumption vector (point values, lounge values, utilisation factors, λ), and the winning allocation — full reproducibility (§74).
4. If evaluator–optimiser gap > 2%: fix binaries to evaluator-true states, re-allocate once, re-evaluate (A.16).
5. `c₀` (direct payment) is always available, so no category is ever forced onto a value-destroying card.

---

## Open questions before Part C (Rules Engine JSON)

1. **Milestone benefit typing** — milestones paying *vouchers* need the utilisation/friction treatment (A.5); milestones paying *statement credit* don't. Proposal: milestone payloads reference the same benefit objects as §15, inheriting u and φ. Confirm.
2. **UPI-on-credit-card** — RuPay UPI spend has its own MCC/cap quirks; treat UPI as a *channel* dimension on rules (already in §6's `channel` field) rather than a category. Confirm.
3. **Default average ticket sizes per category** (drives rounding maths, A.2) — will propose a defaults table in Part C for review.
4. **λ default** — ₹1,500/card/year as the pre-filled complexity penalty, editable. Confirm.
