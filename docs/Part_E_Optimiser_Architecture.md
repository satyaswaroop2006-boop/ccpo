# Credit Card Portfolio Optimiser — Part E
## Optimiser Architecture

Version 0.1 · Consumes Parts A–D. Delivers the C.8 promise: the transparent portfolio-size recommendation rule.

---

# E.0 Where the engine lives — the one-engine placement decision

§52/§74 demand one deterministic calculation engine serving every surface. §51 leans TypeScript-or-Python for calculation and Python for optimisation. These pull against each other: a TypeScript evaluator inside Next.js plus a Python optimiser would force either a duplicate evaluator implementation (two engines — forbidden) or awkward cross-service callbacks in the hot loop (the optimiser calls the evaluator thousands of times per run: MABC scoring, exact evaluation, repair pass).

**Decision: one Python compute service containing both the evaluator and the optimiser, with the evaluator as the shared core.** Next.js is a thin API/UI layer over Supabase and this service; it never computes a rupee. The service is stateless (all state in Postgres per Part D), deployable on Railway alongside your existing infrastructure, horizontally scalable because enumeration is embarrassingly parallel.

```
ccpo-compute/
  engine/            # THE engine — sole source of rupee truth
    normalise.py         Stage 1  (grid expansion, UPI decomposition, seasonality)
    eligibility.py       Stage 2  (exclusion scopes → 3 spend views)
    match.py             Stage 3  (selector binding, priority resolution)
    accrue.py            Stage 4  (exact + ticket-approx accrual, rounding)
    caps.py              Stage 5
    thresholds.py        Stages 6–7 (two-pass, prospective/retroactive)
    valuation.py         Stage 8  (routes, friction, v_cons/v_exp/v_opt)
    benefits.py          Stage 9  (card + portfolio dedup)
    costs.py             Stage 10 (fees/GST/waiver, surcharge, forex)
    assemble.py          Stage 11 (NACV, PV, year modes, trace)
    breakpoints.py       compiled breakpoint list (C.0)
  optimiser/
    candidates.py        E.2
    enumerate.py         E.3
    allocate.py          E.4  (inner MILP; PuLP → HiGHS, CBC fallback)
    repair.py            E.7  (threshold repair pass)
    frontier.py          E.9  (frontier + size recommendation rule)
    classify.py          E.8  (KEEP/CLOSE/ADD/DOWNGRADE/HOLD)
    scenarios.py         E.11
    explain.py           E.12
  api/                   FastAPI: /evaluate /optimise /whatif /next-best-spend /marginal
  goldens/               §55 battery + C.9 example-card goldens (CI gate)
```

Solver: **HiGHS via PuLP** (permissive licence, excellent MILP performance at this scale), CBC as fallback. Both open-source; no licensing dependency in the core IP.

# E.1 End-to-end optimisation flow

```
INPUT  spend profile · preferences · constraints · journey (greenfield | wallet)
  1  SNAPSHOT      freeze rule versions, assumptions registry, constraint set
  2  CANDIDATES    coverage-aware + MABC selection            (E.2)
  3  ENUMERATE     subsets within cardinality bounds           (E.3)
  4  ALLOCATE      inner MILP per subset (λ = 0)               (E.4)
  5  EVALUATE      exact engine run per surviving subset       (E.7)
  6  REPAIR        threshold-edge variants, keep best exact    (E.7)
  7  ASSEMBLE      frontier, ICV table, classifications,
                   size recommendation, explanations           (E.8–E.12)
OUTPUT written to optimisation_runs + portfolio_subset_results + evaluation_runs
```

Every arrow is a pure function of the snapshot — re-running step 1's frozen record reproduces the run bit-for-bit.

# E.2 Candidate selection

Inputs: live card universe (from `current_card_versions`), user spend, constraints. Output: 12–15 `card_version_id`s + the always-present outside option c₀.

```
1  HARD INCLUDES   wallet cards (open), must_keep cards, constraint-required
                   cards (e.g. "≥1 RuPay" ⇒ include top-2 RuPay by quick score)
2  HARD EXCLUDES   refuse_use cards; cards failing coarse eligibility flags
                   (§33: invite-only without relationship ⇒ excluded from
                   "attainable" run, retained for the "theoretical" run)
3  STANDALONE      for every remaining card: PV*({c}) via a single-card
                   allocation (cheap LP, no enumeration) → top-8
4  CATEGORY        for each category with spend > ₹25,000/yr: top-2 cards by
   CHAMPIONS       net marginal rupee rate on that category alone
5  MABC            milestone-adjusted best case per C.0 Safeguard 1: for each
                   threshold tier of each card, exact-evaluate the card with
                   eligible spend forced to the tier, drawn from the user's
                   real categories → top-4 by MABC not already selected
6  UNION           dedupe; if > 18, trim from the standalone tail only —
                   champions and MABC picks are never trimmed (coverage
                   guarantee outranks list size)
```

Steps 3–5 all use the exact evaluator, so no card is ever excluded on smoothed numbers. Candidate lists and their inclusion reasons are stored on `optimisation_runs.constraints_snapshot` — "why was card X even considered / not considered" is itself explainable.

# E.3 Portfolio generation (enumeration)

Subsets of the candidate set, sizes 1..N_max (from cardinality mode: `exactly N` enumerates only size N; `up_to N` and `optimiser_decides` enumerate 1..N). Wallet mode adds the current portfolio as a mandatory member of the enumeration (its exact value anchors "IMPROVEMENT VS CURRENT", §36).

Order and economy:

```
for size s = 1 → N_max:
    for each subset S of size s (lexicographic over sorted candidate ids):
        if constraints violate structurally (fee budget on unwaivable fees,
           network requirements unmeetable) → record as infeasible, skip
        allocate(S)  → pv_planned
        evaluate(S)  → pv_exact          (+ repair pass)
        upsert portfolio_subset_results (run_id, subset_key, …)
```

Two economies, both optional and default-on:

- **Cache reuse**: `subset_key + spend hash + rule versions + assumptions version` — What-If Lab and scenario sweeps hit warm rows instead of re-solving.
- **Bound pruning (conservative)**: an admissible upper bound `UB(S) = Σ_c BestCaseNACV(c) − fees` (each card's MABC-style ceiling, ignoring competition for spend — provably ≥ PV*(S)); skip exact evaluation when `UB(S) < 0.9 × best_exact_so_far` *for subsets that share no card with any current top-10 subset*. At ≤ 5,000 subsets this is an optimisation of an already-cheap loop; it exists for the day the candidate cap rises, and it never prunes anything within 10% of the lead, so the frontier and ICV tables stay exact where it matters. Full-sweep mode (no pruning) is a flag for audit runs.

Parallelism: subsets fan out across workers; results are order-independent upserts.

# E.4 Inner allocation MILP

Exactly Part B's model with the subset fixed (`y` removed), λ = 0 (C.8):

- Continuous: `x(c,k,t)`, segment vars `s(c,q,t)`, benefit draws `l(c,b)`.
- Binary: threshold tiers `z(c,j)` (all payload types — reward, waiver, rate-unlock alike, since they are one construct per C.3), retroactive-tier and fill-order binaries where `tier_mode` demands (B.5).
- **Threshold robustness margin**: when `z=1`, eligible spend ≥ `T + buffer(β)` where feasible (C.0), so proposals arrive threshold-safe.
- **Rate-unlock linearisation**: an `activate_rule` payload contributes segments whose rates are conditioned on `z`; prospective unlocks are approximated in-MILP as annual-average activation (exact chronology restored by the evaluator; any gap caught by the 2% repair rule). This is the one place the MILP is knowingly coarser than the engine — documented, bounded, repaired.
- Warm starts: size-s solutions seed size-(s+1) supersets (copy allocation, leave the new card at zero) — cuts solve time roughly in half in practice.
- Time cap per solve: 2 s; on timeout, fall back to greedy-with-milestone-correction for that subset and flag `solver_fallback` on the row (E.14).

# E.5 Milestone interactions — how the hard cases resolve

- **Milestone vs cap tension on one card** ("chasing the milestone pushes spend past the 1% overflow zone"): both are in the same objective; the MILP nets them exactly. No heuristic.
- **Competing milestones across cards** ("₹30k more on B unlocks ₹10k, but that ₹30k earns ₹900 on A"): opportunity cost is not a bolt-on calculation — it is the demand constraint Σx = D itself. The solver only funds B's milestone if the *net* system-wide gain is positive. §27 is satisfied by construction.
- **Mid-year state (wallet mode)**: annual portfolio optimisation always runs steady-state (clean year). Current-year `current_year_progress` feeds only the **Next-Best-Spend endpoint** (E.12), which answers "given what I've already spent this year, where should the next rupees go" via marginal bands — no MILP, evaluator-only, milliseconds.

# E.6 Portfolio overlap prevention

The shared-Need budget (A.9) as constraints `Σ_c l(c,b) ≤ Need(b)`, `l ≤ Entitle·(qualified)`, where gated quotas (Example 11) tie `Entitle` to their qualification threshold's binary — so the solver knows that drawing lounge value from a quarterly-gated card *requires* routing the qualifying spend, and prices that requirement against alternatives. Worked consequence: two premium cards with overlapping lounge quotas and a 6-visit user will never both be credited lounge value; typically the optimiser drops one card entirely because its fee was only justified by double-counted lounges — exactly the failure §42 exists to prevent, and a genuinely differentiating behaviour versus every comparison site.

# E.7 Exact evaluation and the repair pass (placement)

Per subset: run the engine on the MILP allocation → `pv_exact` + trace; compile the subset's breakpoint list from the schema (C.0); generate near-miss/barely-made variants within `buffer(β)`; evaluate variants; keep the max; set `repair_applied` when a variant wins. Empirical budget: 10–30 breakpoints × ≤ 2 variants ≈ ≤ 60 extra evaluator calls per subset, each ~1 ms.

**Ranking, frontier, ICV, and all UI numbers use `pv_exact` only.** `pv_planned` is retained for gap monitoring (optimiser health metric: distribution of |planned − exact| / exact).

# E.8 ICV, classification, and downgrade handling

With the results table populated, for the chosen portfolio P:

```
ICV(c|P)     = pv_exact(P) − pv_exact(P∖{c})        two lookups
ICV(c⁺|P)    = pv_exact(P∪{c⁺}) − pv_exact(P)       two lookups (if enumerated;
                                                     else one extra solve)
Overlap(c|P) = pv_exact({c}) − ICV(c|P)
```

Classification (wallet mode), evaluated per owned card and per top candidate:

```
KEEP        ICV > icv_meaningful (registry, default ₹1,000)
OPTIONAL    0 < ICV ≤ icv_meaningful
CLOSE       ICV ≤ 0, no strategic-feature flag
HOLD        ICV ≤ 0 but sole provider of a user-flagged strategic feature
            (zero-forex / UPI / status / acceptance) → surfaced with the ₹ cost
            of holding: "keeping this costs you ₹X/yr for feature Y"
ADD         candidate with ICV > icv_meaningful and eligibility ≠ unlikely
DOWNGRADE   see below
```

**Downgrade** = replacement within a card family. Small additive schema element (Part D §D.3 pattern): `cards.family_key` groups variants (e.g. a premium card and its no-fee sibling). A DOWNGRADE recommendation is emitted when, for an owned card c with family sibling c′: `pv_exact(P∖{c}∪{c′}) > pv_exact(P)` — an ordinary REPLACE evaluated through the same tables, labelled as a downgrade because the family link exists. No special maths.

# E.9 Efficient frontier and the transparent size-recommendation rule (the C.8 promise)

Frontier: `SELECT size, max(pv_exact) … GROUP BY size` over the run's results, with each size's winning subset and allocation attached. Raw values shown to the user always (your requirement: expose the frontier before any recommendation).

**Recommendation rule — an ordered, fully visible checklist.** Recommend the largest n ≤ N_tol (the user's complexity tier) such that **every incremental step 1→2→…→n passes all three tests**:

```
Step n → n+1, with ΔV = V(n+1) − V(n)  (net, exact values):

T1  MATERIALITY        ΔV ≥ max( abs_floor , rel_pct · V(n) )
                       defaults: abs_floor = ₹2,000/yr, rel_pct = 3%
T2  FEE-AT-RISK        let ΔF = additional annual fees committed (gross, pre-waiver)
                       require  ΔGrossBenefit / ΔF ≥ 1.5   OR   ΔF ≤ ₹1,000
                       (a step whose net gain rides thin margins over large
                        committed fees is fragile even when ΔV is positive)
T3  SCENARIO FLOOR     when Low/High sweeps exist (E.11):
                       ΔV under the Low-spend scenario ≥ 0
                       (the extra card must not become a liability if
                        spending contracts 20%)
```

All three parameters live in the assumptions registry (C.7), never hidden. The UI renders each step as a row — "3rd card: +₹7,800/yr ✓ material · fees +₹1,500, benefit cover 6.2× ✓ · still +₹4,100 at low spend ✓" / "4th card: +₹2,100 ✗ below ₹2,000-or-3% bar" — so the recommendation is *literally* its own explanation. λ stays available as an advanced parameter for users who want a per-card inconvenience price; it defaults to 0 and never operates invisibly (C.8 honoured).

Tolerance interaction: if the user's tier caps n below where tests still pass, say so ("a 4th card would add ₹6,400/yr, but you asked for ≤ 3 — change tolerance to see it"). Never silently expand.

# E.10 Greenfield vs wallet — one engine, two entry states

| | Greenfield (BUILD MY PORTFOLIO) | Wallet (OPTIMISE MY WALLET) |
|---|---|---|
| Candidate set | full universe | universe + owned cards hard-included |
| Enumeration | sizes per cardinality mode | + current portfolio as anchor subset |
| Fees | public card_version fees | `fee_override` / `lifetime_free` per card (§62) — a lifetime-free card's ICV floor is ~0, so CLOSE is rarely recommended for it, correctly |
| Constraints | user constraint doc | + must_keep / refuse_use rows |
| Output | optimal N-card portfolio + playbook + frontier | KEEP/CLOSE/ADD/DOWNGRADE + playbook + "improvement vs current" |
| Eligibility (§33) | two runs when they differ: attainable (eligibility-filtered) is primary; theoretical shown with the gap explained | same |

Identical code path; the journey is an input flag plus constraint rows — §22a's requirement, mechanically true.

# E.11 Scenarios and robustness

Low/Expected/High = spend vector × {0.8, 1.0, 1.2} (registry-configurable; later per-category scenario editing). Each is one more sweep over the *same* candidate set with warm cache. Reported per portfolio:

```
Robustness = V_low / V_expected      (headline: "keeps 87% of its value if
                                      your spending drops 20%")
```

plus rank stability (does the recommended portfolio stay top-3 across scenarios). Feeds T3 above. Deliberately simple for MVP — §65 allows a richer metric later; the sweeps and storage already support it.

# E.12 Explainability surfaces (§37–39, §26)

- **Why this card**: the C.10 trace, aggregated per card into the §37 ledger (base / bonus / milestones / benefits / fees / costs), with every line carrying rule keys and source refs.
- **Threshold analysis (§38)**: read the *binding constraints* of the inner MILP + the repair-pass records: which caps were hit (binding segment), which thresholds were funded vs left short and by how much. Crossovers between competing cards computed by 1-D scans: vary one driver (e.g. annual travel spend) across a grid, re-evaluate the top-2 portfolios (evaluator only — no MILP), report the crossing point: "Card A wins below ₹3.2L travel; Card B above."
- **"What could change this?"**: the same 1-D scan machinery over the most sensitive drivers (identified from trace magnitudes), reported as the smallest realistic change that flips the ranking.
- **Marginal bands / Next-Best-Spend (§26, §39)**: evaluator-only endpoint. Wallet `current_year_progress` seeds threshold and cap states; for each held card and each of Δ ∈ {1k, 10k, 50k}, exact delta-value → "route your next ₹38,000 to Card B (unlocks ₹10,000 milestone, ≈ 27% net after opportunity cost vs Card A's 2%)". Milliseconds; suitable for the future TODAY'S BEST CARD surface (§67) without architectural change.
- **The marginal value curve (§39)**: a spend sweep on one card through the evaluator, plotting exact value with its kinks; every kink annotates itself from the breakpoint list.

# E.13 Performance budget (MVP targets)

| Stage | Budget |
|---|---|
| Candidate selection (incl. MABC) | < 1 s |
| Full sweep, 15 candidates, sizes ≤ 5, monthly grid | < 30 s cold, < 3 s warm cache |
| Annual-grid shortcut (uniform seasonality) | < 8 s cold |
| Single evaluate / what-if / next-best-spend | < 100 ms |
| Repair pass overhead | < 10% of sweep |

Sweep runs async: `optimisation_runs.status = running` with progressive row upserts, so the UI can stream the frontier as sizes complete (size-1 results appear in the first second).

# E.14 Failure modes

- **Solver timeout on a subset** → greedy-with-milestone-correction fallback, `solver_fallback` flag on the row, excluded from "best" selection unless it still wins by > 5% (then surfaced with the flag).
- **Infeasible constraints** (fee budget below cheapest compliant portfolio, contradictory network demands) → no silent relaxation; return a constraint report naming the binding conflict and the cheapest relaxation ("raising fee budget to ₹6,000 admits 4 portfolios").
- **Stale rules mid-run** → impossible by snapshot: the run pinned its card_version IDs at step 1; publishes during a run affect only subsequent runs.
- **Evaluator/optimiser gap > 2% after repair** → row flagged `gap_exceeded`, telemetry alert; the exact value still rules the ranking, so users are never shown optimistic numbers.

# E.15 Forward pointer

Part F consumes: the frontier table (§23 chart), the checklist rows of E.9 (size recommendation UI), the classification set (KEEP/CLOSE/ADD screen), marginal bands (playbook and next-best-spend surfaces), and the trace ledgers (why-this-card panels). Every screen in Part F is a rendering of a stored structure defined here — no screen computes anything.
