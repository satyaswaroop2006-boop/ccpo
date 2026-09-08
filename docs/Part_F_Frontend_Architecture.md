# Credit Card Portfolio Optimiser — Part F
## Frontend Architecture

Version 0.3 · **APPROVED** 2026-09-08 (docs/DECISIONS.md #160/#162) — five
scope/stack/auth decisions resolved directly with Satya, then a final
read-through found and closed one real technical gap (F.4/F.8's
`bundle_path` note) before sign-off, same posture Part I took before any
`compute/ingest/` code was written. Consumes
Part E §E.15's own forward pointer ("Part F consumes: the frontier table,
the checklist rows, the classification set, marginal bands, and the trace
ledgers — every screen is a rendering of a stored structure, no screen
computes anything") and Part I §I.11's own forward pointer (a proper review
UI for `source_links`, superseding the CLI/Supabase-Table-Editor MVP). Both
were written speculatively, before Part F existed as a document — this
closes that gap the way Part I closed Part C §C.9's own forward reference.

**Decisions locked in for this version** (each section below reflects these,
not a proposal any more):
1. **Scope**: narrow v1 — Catalog + Calculator + Ingestion Review only.
   Wallet persistence, saved spend profiles, and run history all stay
   deferred (F.1).
2. **Audience**: this is building toward a real public product, not staying
   an internal-only tool — even though v1's SCOPE is narrow, its
   ARCHITECTURE (auth, stack) is chosen with that future in mind, not for
   internal-tool convenience.
3. **Stack**: Next.js + TypeScript + Supabase Auth (F.5) — chosen over a
   plain Vite SPA specifically because of decision 2.
4. **Auth**: real Supabase Auth wired up now, but the Review/Publish
   endpoints are restricted to a single allow-listed account (Satya's) —
   real plumbing, not a throwaway shortcut, reusable when public accounts
   arrive later (F.6).
5. **Publish**: the Ingestion Review screen gets a guarded Publish button
   once every `source_link` on a card_version is approved — not CLI-only
   (F.2.3, F.7).

---

# F.0 The one hard rule, stated first

**No screen in this frontend computes a rupee value.** Every number a user
sees — NACV, gross reward value, ICV, a frontier point, a recommendation —
is read verbatim from a `compute/` API response. This is CLAUDE.md's
non-negotiable rule 1 ("Every rupee-valued number is computed in
`compute/engine/`. No financial math in ... the frontend") applied
concretely:

- The frontend may format, sort, filter, and chart numbers it receives.
- The frontend may NEVER re-derive a number the API didn't already compute
  — no "quick estimate while we wait," no client-side reward-rate
  arithmetic, no rounding a raw spend number into a projected value locally.
- If a screen needs a number the API doesn't yet return, that's an API gap
  to close in `compute/` (F.3), never a reason to compute it in JavaScript.

The second hard rule, specific to this document's own motivation (see F.4):
**no UI control may write a foreign key.** A reviewer approves or rejects a
`source_link`; the UI never exposes an editable `source_id` field, a raw
table-edit grid, or any control whose action isn't a single named,
server-validated state transition. This is a direct response to
`docs/DECISIONS.md` #158 — three `source_links` rows had their `source_id`
silently corrupted during a review pass conducted in Supabase's raw Table
Editor, two of them already `approved` by the time the corruption was found.
A proper review UI structurally cannot reproduce that failure mode, because
it never gives the reviewer a column to accidentally overwrite.

# F.1 Scope for v1 (DECIDED — narrow, nothing pulled forward)

Three screens, each a thin, honest rendering of something `compute/` already
computes or a workflow already specified:

1. **Catalog** — browse published cards (read-only, no math; public, no
   auth required — see F.6).
2. **Calculator** — a user pastes/enters a spend profile and gets an
   `/evaluate` result for one card or an `/optimise` recommendation across
   the live catalog. Stateless: nothing is saved server-side (F.6).
3. **Ingestion Review** — the Part I §I.11 review UI: list unreviewed
   `source_links` grouped by card, each row shows its cited source (title,
   URL, snippet if available) side by side with the field it's evidencing,
   two buttons: Approve / Reject, plus a guarded Publish action once a
   card_version's own links are all approved (F.2.3). This is the one
   screen with side effects, and its side effects are deliberately narrow
   (F.4), gated to a single allow-listed account (F.6).

**Explicitly deferred, not forgotten** (each needs backend work this
document doesn't propose building yet):

- Persisted user wallets (`user_wallet_cards`), saved spend profiles
  (`user_spend_profiles`/`user_spend_items`) — schema exists (Part D), no
  code reads/writes it yet, and Phase 3/4's own status notes repeatedly
  deferred "wallet mode" (docs/DECISIONS.md #10/#61).
- `evaluation_runs`/`optimisation_runs`/`portfolio_subset_results`
  persistence — same deferral, noted at the end of both Phase 3 and Phase
  4's own status blocks in CLAUDE.md ("No persistence yet").
- **Public sign-up / multi-tenant accounts** — Supabase Auth is wired up in
  v1 (F.6), but only to gate the Review/Publish screen behind Satya's own
  allow-listed account. No sign-up flow, no other user can create an
  account or log in yet. Part D Decision 9's RLS posture
  (`auth.uid()`-scoped `user_*` tables) already supports real multi-tenant
  accounts whenever that's built — v1 just doesn't expose it.
- The explainability surfaces Part E §E.12 describes in detail (why-this-
  card ledger, threshold/crossover analysis, marginal value curve) — the
  underlying logic exists (`optimiser/explain.py`, Phase 4 Slice 7) but has
  no HTTP endpoint yet (docs/DECISIONS.md #100: "left for future dedicated
  endpoints"). Out of scope for v1 until those endpoints exist (F.3).

# F.2 Screens

## F.2.1 Catalog

Purpose: let Satya (or, later, any user) see what's actually live without
opening Supabase directly.

- List of published cards: name, issuer, network, annual fee, joining fee,
  currency, one-line reward summary (base rate + top accelerated rate, both
  read from the card's own `earning_rules`, not recomputed).
- Card detail view: full rule breakdown (earning rules, caps, exclusions,
  thresholds, benefits, surcharges) rendered from the same structures
  `engine/card_bundle.py` already parses — a direct, formatted dump of the
  rule vocabulary Part C defines, not a new summarization layer.
- No write actions. No auth required (Part D Decision 9: catalog tables are
  RLS-on, read-for-everyone).

**New API need**: `GET /cards` (list) and `GET /cards/{key}` (detail) — the
`CardRepository` interface (`app/repository.py`) already has both
`get_all_card_bundles()` and `get_card_bundle(card_key)` (verified present
on both the synthetic and Postgres implementations), so this is a thin
read-only wrapper over each, not new engine logic.

## F.2.2 Calculator

Purpose: the actual product surface — "what should I do with my spending."

- Spend input: a form mirroring `EvaluateRequest`/`OptimiseRequest`'s own
  `SpendItemIn` shape (category, optional channel/merchant_group/geography,
  annual amount) — not a new vocabulary, a form over the existing one.
- Single-card mode: pick one published card, submit, render
  `EvaluateResponse` (NACV steady-state/year-1, gross reward value,
  benefit value, fee, the trace lines) — every field already exists in
  `app/schemas.py`.
- Portfolio mode: submit spend, no card picked, call `/optimise`, render:
  - the frontier (`FrontierPointOut[]`) as a size-vs-value chart (Part E
    §23's own chart, per §E.15's forward pointer),
  - the size-recommendation checklist (`RecommendationStepOut[]`, each
    step's own `explanation` string is already plain-rupees prose per
    Part E §E.9 — render it verbatim, don't re-summarize it),
  - the recommended portfolio's card keys and `recommended_pv_exact`,
  - the classification screen (`CardClassificationOut[]` for
    `classification_owned` + `classification_candidates`) as a KEEP /
    OPTIONAL / CLOSE / HOLD / ADD / DOWNGRADE badge list (Part E §E.8, per
    §E.15's forward pointer),
  - robustness (`RobustnessOut | None`) as the "keeps X% of its value if
    your spending drops 20%" headline Part E §E.11 specifies verbatim.
- `excluded_cards` rendered honestly (per docs/DECISIONS.md #97/#98's own
  "never silently dropped" posture) — a small "N cards couldn't be
  evaluated" disclosure with each one's own reason, not hidden.

**No new API need** — `/evaluate` and `/optimise` already return everything
this screen renders.

## F.2.3 Ingestion Review

Purpose: replace the Supabase Table Editor as the tool used to approve
`source_links` — directly motivated by docs/DECISIONS.md #158.

- List unreviewed `source_links`, grouped by card (mirrors `ingest
  review-queue`'s own grouping) — but rendered instead of printed.
- Each row shows: the entity being evidenced (e.g. "earning_rule
  `accelerated_10pt`"), the field's own value as drafted, the cited
  source's title/URL/type/confidence, and — where the storage backend
  makes it feasible — an inline preview of the captured document (or at
  minimum a link to the stored snapshot, never the live URL, since the
  whole point of CAPTURE per Part I §I.1 is reviewing the snapshot, not
  trusting the page hasn't changed since).
- Two actions per row: **Approve** / **Reject (with a required note)**.
  Each action is a single, narrow API call that updates `reviewer_status`
  (and, for reject, `previous_rule_note`) and NOTHING else — see F.4 for
  why this is the whole point of building this screen at all.
- A card-level "all approved, ready to publish" indicator, computed by
  calling the SAME gate check `ingest publish` itself runs (SS I.8) — not
  a re-implementation of it — so the UI's notion of "ready" can never drift
  from the CLI's.
- **Publish button**, shown only once that gate passes. Clicking it
  requires a second, explicit confirmation (e.g. typing the card's own key
  to confirm, mirroring how destructive/irreversible actions are gated
  elsewhere in this repo's own working style) before the request fires —
  publish is irreversible (Part D Decision 2), and the UI must not make an
  irreversible action feel as casual as Approve/Reject. This is still a
  human-triggered action; it removes the CLI round-trip, not the
  deliberateness.

**New API need**: `GET /review-queue` (or scoped per card_version),
`POST /source-links/{id}/approve` / `POST /source-links/{id}/reject`, and
`POST /card-versions/{id}/publish` — see F.4.

# F.3 API surface — what exists, what's new

| Endpoint | Status | Used by |
|---|---|---|
| `POST /evaluate` | exists | Calculator (single-card mode) |
| `POST /optimise` | exists | Calculator (portfolio mode) |
| `POST /next-best-spend` | exists | not used in v1 (needs a wallet/held-cards concept to be useful — deferred with wallet mode, F.1) |
| `GET /health` | exists | ops only |
| `GET /cards`, `GET /cards/{key}` | **new, thin wrapper**, public | Catalog |
| `GET /review-queue` | **new, thin wrapper over `ingest review-queue`'s own query**, auth-gated | Ingestion Review |
| `POST /source-links/{id}/approve`, `/reject` | **new**, auth-gated | Ingestion Review |
| `POST /card-versions/{id}/publish` | **new, thin wrapper over `ingest.publish`'s existing gate**, auth-gated | Ingestion Review |

The four new endpoints are all thin — no new engine or optimiser logic,
just HTTP surface over queries/mutations `compute/ingest/` already has as
CLI functions (`review.py`'s query, `publish.py`'s existing gate-check
function called directly rather than reimplemented, and narrow mutations
`ingest`'s own tooling deliberately doesn't expose yet per Part I §I.5's
"never Claude, never the same automated step" — a human clicking
Approve/Publish in a browser is the same human-only action `ingest
review-queue`'s own docstring already anticipates being superseded, not a
new automation). The auth-gated ones all sit behind F.6's single
allow-listed account check; `/cards*` stay public, matching Catalog's own
no-auth design (F.2.1).

Deferred (F.1): endpoints for `optimiser/explain.py`'s crossover/curve/
ledger surfaces (docs/DECISIONS.md #100), any wallet/spend-profile
persistence endpoints, `evaluation_runs`/`optimisation_runs` history
endpoints.

# F.4 Why the review UI's write path is narrow, in detail

This is the one place in this document responding directly to a live
incident, so it's worth being explicit about the design, not just asserting
"it'll be safer."

The corruption in #158 was possible because the review step happened in a
tool (Supabase's raw Table Editor) that presents `source_links` as an
ordinary editable spreadsheet — every column, including the foreign key
`source_id`, is a cell a reviewer can click into and change, whether
intentionally or via a stray paste/drag across rows. Nothing about that
interface distinguishes "the column you're supposed to touch"
(`reviewer_status`) from "the column that silently rewrites which document
is evidence for this fact" (`source_id`).

The fix isn't "be more careful in Supabase" — it's removing the
opportunity. `POST /source-links/{id}/approve` and `.../reject` are the
ONLY way this UI ever mutates a `source_links` row, and each is
implemented server-side as an `UPDATE source_links SET reviewer_status =
..., previous_rule_note = ... WHERE id = ...` — a fixed statement, no
column names ever come from request input beyond the note text.
`source_id` is never writable through this UI at all, full stop; fixing a
genuinely wrong citation (as #158 needed) stays a direct DB action for
Satya (or a re-run of `ingest link` against a corrected bundle), exactly as
it was this time — this screen makes the COMMON case (approve/reject) safe
by construction, it doesn't try to also cover the rare "the citation itself
is wrong" case, which is Part I's own DRAFT-stage concern, not REVIEW's.

The same narrowness applies to `/card-versions/{id}/publish`: it calls
`ingest.publish.publish_card_version` directly, the same code path the
CLI uses, rather than a second implementation of SS I.8's rules that
could quietly drift from the CLI's over time. The endpoint either flips
`status='published'` (gate passed) or returns the same "REFUSED —
publish gate failed... naming exactly which condition failed" error the
CLI already produces — no new publish logic, no new failure modes.

**A real gap found during this document's final read-through (2026-09-08,
after docs/DECISIONS.md #161 landed), not present when v0.1 was first
drafted**: `publish_card_version` now takes a required `bundle_path`
argument (#161's own source-provenance cross-check needs the original
bundle FILE, not just the database, to verify a citation is still
correct) — but nothing in the schema records which bundle file a given
`card_version` was linked from. The CLI sidesteps this because a human
supplies `--bundle` by hand each time; an API endpoint driven by a
button click has no human to ask. **Resolution, needed before Slice 7
(F.8) can be built, not before this document's own approval**: add a
`bundle_path` column to `card_versions`, populated by `ingest link` at
LINK time (the bundle file's own path is already known at that moment,
it's just never been persisted) — a small, additive schema change, not
a redesign of anything Part I or this document already decided. Rows
linked before this column exists (every real card published so far)
would need either a one-time backfill from their own known file paths
or to stay CLI-publishable-only until backfilled; this document doesn't
resolve which, since it's a Part I/D-layer schema question, not a
frontend one — flagged here because F.2.3's Publish button surfaced it,
same as #158 surfaced the review-UI need itself.

# F.5 Tech stack (DECIDED — Next.js + TypeScript + Supabase Auth)

No stack is specified anywhere in Parts A–E/I, so this was a genuine
decision, not an extraction from existing docs — resolved directly with
Satya given F.1's audience decision (building toward a real public
product, not staying an internal tool):

- **Next.js + TypeScript** for the frontend — chosen over a plain Vite SPA
  specifically because of the public-product direction: SSR gives the
  public Catalog page real SEO (matters for a consumer product reaching
  people searching for card comparisons, doesn't matter for an internal
  tool), and Next.js's own official Supabase integration patterns give a
  direct, well-trodden path from today's single-allow-listed-account auth
  (F.6) to real multi-tenant sign-up later, without a framework migration
  in between.
- **Supabase Auth** for the one screen that needs it (Ingestion Review) —
  the SAME Supabase project already backing `compute/`'s own database, not
  a second identity system. Session tokens verified server-side (Next.js
  API routes or middleware) before any of F.3's auth-gated endpoints run.
- The Catalog/Calculator screens are chart-and-table-heavy (frontier
  charts, classification badges, trace tables) — TypeScript gives the
  request/response shapes in `app/schemas.py` a natural mirror on the
  client side (hand-written types or generated from FastAPI's own OpenAPI
  schema — cheap either way, avoids the shapes drifting apart silently).
- Talks directly to the existing FastAPI service (`app/main.py`) — no
  second backend framework, no BFF layer beyond Next.js's own API routes
  where a server-side auth check is needed. The four new endpoints (F.3)
  are added to the same `app/` package.
- No decision made here on hosting/deployment — out of scope for an
  architecture document, and premature before v1 exists to deploy.

# F.6 Data flow & auth model for v1

The Catalog/Calculator surfaces are public by design (F.1's audience
decision); Ingestion Review is not — that split, not "everything
unauthenticated," is the v1 model:

- **Catalog**: no auth, read-only, public (RLS already permits
  read-for-everyone on catalog tables per Part D Decision 9).
- **Calculator**: no auth, stateless — spend input lives only in the
  browser tab's own state for the duration of the session; nothing is
  persisted server-side (no `evaluation_runs` row, per F.1's deferral).
  Refreshing the page loses the input; this is a known, accepted v1
  limitation, not an oversight. Deliberately anonymous-first: since F.5
  already commits to Supabase Auth for Review, attaching a logged-in
  user's saved spend profile to this SAME screen later (once wallet
  persistence is built, F.1) is an additive change, not a rewrite.
- **Ingestion Review**: real Supabase Auth (F.5), but v1 has no sign-up
  flow and no general user base — the four auth-gated endpoints (F.3)
  check the verified session's user id against a single allow-listed
  value (Satya's own account, held as a small server-side config value,
  not hardcoded literally into the endpoint logic). This is real auth
  plumbing (JWT verification, session handling), not a shortcut — the
  allow-list is the only part that's temporary; replacing it with a real
  roles/permissions check later (when a second reviewer exists) doesn't
  touch the auth mechanism itself, only the one check at the end of it.

# F.7 Non-negotiable rules, extended to the frontend

Restating CLAUDE.md's own rules as they bind this layer specifically:

1. **One engine** (rule 1): no rupee value is ever computed outside an API
   response — F.0.
2. **Goldens gate everything** (rule 2): doesn't apply directly to UI code
   (there's no golden scenario for a React component), but any NEW backend
   endpoint this document proposes (F.3) is still `compute/` code and
   still needs its own tests before merging — a thin wrapper endpoint
   still gets a test asserting it returns what the underlying function/
   query actually returns.
3. **Published = immutable** (rule 3): the Ingestion Review screen's
   Publish button (F.2.3) calls the SAME gate `ingest publish` already
   enforces (SS I.8) — it doesn't loosen the irreversibility, it just
   moves where the human clicks the button. A published card_version is
   exactly as immutable whether publish was triggered from the CLI or the
   UI (Part D's own trigger enforces this at the database level either
   way, not something either caller could bypass).
4. **Synthetic vs real** (rule 4): the Catalog screen renders whatever
   `CardRepository` returns — if `DATABASE_URL` is unset it'll show the
   synthetic fixtures, same as the API does today; no special-casing
   needed, no risk of blending the two since the repository selection
   logic already lives in one place (`get_repository`).
5. **Determinism** (rule 5): satisfied by construction — F.0 means the
   frontend never introduces its own non-determinism (no client clock
   reads feeding a computation, no client-side rounding).

# F.8 Build order (proposed slices, mirroring Phase 2–5's incremental discipline)

1. **Slice 1 — DONE (2026-09-08, docs/DECISIONS.md #163)**: the
   `GET /cards`, `GET /cards/{key}` endpoints + tests — pure `compute/`
   work, no frontend, no auth (public). Found along the way: `CardRuleBundle`
   carries no display metadata at all (by design — it's an engine
   dataclass), so this needed a genuinely separate `CardSummary` read
   path, not a reuse of `get_card_bundle`; the detail endpoint's rule
   breakdown is a generic recursive dataclass serializer
   (`app/schemas.py::_jsonable`), not ~10 hand-written Pydantic models,
   to avoid the same hand-duplicated-schema drift risk F.5 already
   flagged. Verified against `TestClient` (8 new tests) and a real
   running server against the live catalog (`GET /cards/prime_sbi`
   rendered correctly).
2. **Slice 2**: Catalog screen against Slice 1. No forms, no mutations,
   no auth — good first slice to stand up the Next.js project itself and
   validate the stack choice (F.5) before investing in heavier screens.
3. **Slice 3**: Calculator, single-card mode (`/evaluate`) — smaller than
   portfolio mode, exercises the spend-input form once before Slice 4
   reuses it. Still no auth (public, per F.6).
4. **Slice 4**: Calculator, portfolio mode (`/optimise`) — the frontier
   chart, checklist, classification badges, robustness headline.
5. **Slice 5**: Supabase Auth wiring — session handling in Next.js, the
   server-side allow-list check (F.6), applied to a placeholder endpoint
   first to prove the mechanism works before any real mutation sits behind
   it.
6. **Slice 6**: `GET /review-queue` + `POST /source-links/{id}/approve`/
   `/reject` (F.3), auth-gated via Slice 5's mechanism, + the Ingestion
   Review screen's list/approve/reject UI.
7. **Slice 7**: `POST /card-versions/{id}/publish` (F.3, F.4) + the
   guarded Publish button (F.2.3) — last, since it's the one irreversible
   action and deserves the most deliberate review before shipping.
   **Prerequisite, found during this document's final read-through**: a
   `bundle_path` column on `card_versions` (F.4's own note) needs adding
   and backfilling before this slice can call `publish_card_version` at
   all — a small Part I/D-layer schema change, sequenced before Slice 7
   starts, not blocking Slices 1–6.

Each slice: built, manually verified against a running `uvicorn` instance
(per this repo's own "show what changed, which tests pass, one worked
example" working-style rule), reported back before the next slice starts —
same cadence as every Phase 2–5 slice, adapted from "golden scenario" to
"screenshot + a walkthrough of the worked example" since there's no
golden-JSON equivalent for a UI screen.

# F.9 Worked example (illustrative — describes the intended v1 experience)

A user (in v1, this is Satya) opens the Calculator, enters:
`grocery: ₹3,00,000/yr`, `ecommerce: ₹6,00,000/yr`. They leave the card
picker empty (portfolio mode) and submit.

The screen calls `POST /optimise` with that spend and default assumptions.
The response renders as: a frontier chart climbing from 1 card to N cards,
flattening after size 2; a checklist showing size 1→2 passed T1/T2 (a real
₹2,000+ jump, fee more than covered) but size 2→3 failed T1 (materiality
threshold) with `explanation` text quoted directly, unmodified, from the
API; a recommended 2-card portfolio with its own `recommended_pv_exact`;
a classification list showing those 2 cards as KEEP and 3 more candidates
as CLOSE/HOLD with their own ICV numbers; a robustness line reading
"keeps 91% of its value if your spending drops 20%, stays in the top 3
across all three scenarios." Every number on this screen traces to a field
already in `OptimiseResponse` — nothing here is invented for this example
beyond the input numbers themselves (same "illustrative, not real data"
posture Part I §I.10's own worked example takes).

# F.10 Decisions log (resolved 2026-09-08, docs/DECISIONS.md #160)

The five open questions this document originally raised were resolved
directly with Satya, one at a time, before any code follows:

1. **Scope**: narrow v1 (Catalog + Calculator + Ingestion Review) —
   nothing from the deferred list pulled forward.
2. **Audience**: building toward a real public product, not an
   internal-only tool — shapes decisions 3–5 below even though v1's own
   scope stayed narrow.
3. **Tech stack**: Next.js + TypeScript + Supabase Auth, chosen over a
   plain Vite SPA because of decision 2.
4. **Auth**: real Supabase Auth wired up now, restricted to a single
   allow-listed account (Satya's) rather than a general sign-up flow —
   real plumbing, not a localhost-only shortcut.
5. **Publish**: a guarded Publish button ships in v1's Ingestion Review
   screen, calling the same gate-check the CLI already uses (F.4) — not
   CLI-only.

**Final read-through (2026-09-08, docs/DECISIONS.md #162)** cross-checked
every technical claim above against the live codebase — including the
publish-gate hardening (#161) that landed concurrently with this
document's own drafting — and found one real gap: `publish_card_version`
now requires a `bundle_path` the schema doesn't yet track anywhere (F.4,
F.8 Slice 7). Fixed by adding that as an explicit Slice 7 prerequisite,
not by silently glossing over it. With that closed, **this document is
APPROVED** — Slice 1 (F.8) may begin.
