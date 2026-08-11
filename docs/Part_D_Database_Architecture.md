# Credit Card Portfolio Optimiser — Part D
## Database Architecture (PostgreSQL / Supabase)

Version 0.1 · Companion file: `0001_init.sql` (full migration). This document explains the decisions; the SQL is the specification.

---

# D.0 Architectural decisions

**Decision 1 — Typed shell, JSONB payloads.** Selectors, accruals, windows, threshold payloads, and trace nodes are JSONB columns inside strongly-keyed relational tables. Rationale: the engine loads rules **wholesale by card_version** — it never queries "all rules whose selector mentions grocery" — so relational decomposition of selectors would add joins and migration friction for zero query benefit. Everything with referential integrity requirements (card ↔ version ↔ rule ↔ cap ↔ tier ↔ benefit ↔ currency ↔ route) is a real foreign key. Everything that is engine vocabulary rides as validated JSONB (validation happens at publish time via the C.11 linting battery, not in the database). Consequence: **no GIN indexes needed in MVP** — access paths are all by ID and status.

**Decision 2 — Immutability is enforced by the database, not by convention.** Published catalog rows reject UPDATE/DELETE via triggers, with exactly two permitted mutations: `published → deprecated`, and setting `effective_to` when a successor version is published. Child rule rows (earning rules, thresholds, caps, exclusions, benefits, surcharges) reject mutation whenever their parent `card_version` is published. This makes §47's "never silently overwrite" a mechanical guarantee — a bug in an ingestion script physically cannot corrupt history.

**Decision 3 — Versioning unit = the card_version bundle.** A devaluation is a new `card_versions` row (new `version_no`, new `effective_from`) with a fresh set of child rules, typically copied-then-edited from the predecessor in `draft` status, walked through the C.11 golden tests, then published. `effective_to` on the old version is set in the same transaction. The view `current_card_versions` resolves "the live version today"; the devaluation engine (§68) diffs two version bundles directly.

**Decision 4 — Source tracking via a soft-polymorphic link table.** `source_links(entity_type, entity_id, source_id, confidence, reviewer_status, …)` attaches any source to any catalog row. The polymorphic pair is not FK-enforceable — accepted trade-off, mitigated by a nightly orphan-check job — in exchange for one uniform provenance model across seven rule tables, matching §4's field list (confidence, reviewer status, effective dates, evidence notes) without seven parallel link tables. Captured page snapshots live in Supabase Storage; `sources.storage_path` points at them.

**Decision 5 — The enumeration cache is a first-class table, not a cache.** `portfolio_subset_results` stores every enumerated subset's planned value, exact value, and allocation, keyed by a canonical sorted subset key. This is deliberately promoted from "solver internals" to schema because Part B's product surface is built on it: the efficient frontier is `GROUP BY size / MAX(pv_exact)`, every ICV is two row lookups, and the What-If Lab's add/remove/replace queries are index scans. Persisting it also makes optimisation runs auditable and resumable.

**Decision 6 — Reproducibility columns everywhere they matter.** `evaluation_runs` stores: engine version, assumptions snapshot ID, the exact `card_version` IDs evaluated (immutable by Decision 2), wallet overrides, the allocation, and an input hash. Re-running a stored run byte-identically is a SELECT away — §74's reproducibility requirement, structurally.

**Decision 7 — Text + CHECK constraints instead of Postgres enums.** Enums in Postgres are painful to extend inside transactions and awkward through Supabase migrations; every closed vocabulary here (`status`, `tier_mode`, `overflow`, `route_type`, …) is `text` with a CHECK. Same integrity, easier evolution.

**Decision 8 — Dual identity: UUID primary keys + human keys.** Every catalog row has a `uuid` PK and a stable human `key` (`"er_ecom_5pct"`), unique within its card_version. Rules reference each other by key in JSONB payloads (readable, diffable, matches Part C's examples); the database joins on UUIDs. Ingestion and review tooling read like the spec; the machine gets integrity.

**Decision 9 — RLS posture.** All `user_*`, `evaluation_*`, and `optimisation_*` tables: row-level security on, `user_id = auth.uid()` policies for select/insert/update/delete. Catalog tables: RLS on with read-for-everyone policies and **no write policies** — writes happen only through the service role (ingestion pipeline, admin tooling). Familiar Supabase pattern, same as FarmRent's.

**Decision 10 — Transactions table ships now, empty.** `user_transactions` exists in the initial migration even though MVP is category-mode, because Part A's evaluator is already dual-mode and retrofitting the FK web later (wallet card linkage, channel/geography fields) is costlier than carrying an empty table.

# D.1 Table map

```
CATALOG (service-role writes, world-readable)
  issuers ─┬─ cards ─┬─ card_versions ─┬─ earning_rules ──┬─ earning_rule_caps ─ caps
           │         │                 ├─ thresholds ───── threshold_tiers
           │         │                 ├─ exclusions
           │         │                 ├─ benefits (→ thresholds for qualification)
           │         │                 └─ surcharges
           │         └────────────────── (identity, URLs, lifecycle)
           └─ reward_currencies ── redemption_routes
  sources ── source_links (soft-poly → any catalog row)
  assumption_versions (registry snapshots)

USER (RLS: owner only)
  user_profiles ── user_preferences
  user_spend_profiles ── user_spend_items
  user_wallet_cards (→ cards; overrides, anniversary, progress, balances)
  user_transactions (advanced mode)

COMPUTE (RLS: owner only)
  optimisation_runs ── portfolio_subset_results (→ evaluation_runs)
  evaluation_runs ── evaluation_traces (1:1, split for row size)
```

# D.2 Index strategy (summary — full list in SQL)

- Every FK column indexed.
- `card_versions (card_id, status)` + partial index on `status = 'published'` with effective-date columns — powers `current_card_versions`.
- `portfolio_subset_results (optimisation_run_id, size, pv_exact DESC)` — frontier queries; `(optimisation_run_id, subset_key)` unique — ICV lookups.
- `evaluation_runs (user_id, created_at DESC)` and `(input_hash)` — history and dedup.
- `user_spend_items (spend_profile_id)`, `user_wallet_cards (user_id)` partial on `closed_at IS NULL`.
- `source_links (entity_type, entity_id)` and `(source_id)`; partial on `reviewer_status = 'unreviewed'` for the review queue.
- No JSONB GIN indexes (Decision 1); revisit only if a real query pattern emerges.

# D.3 What deliberately is NOT in this schema yet

- Eligibility/underwriting data (§32–33) — later migration, isolated tables, no coupling to the engine.
- Merchant-offer tables (§43) — `offer_confidence` gets its own table when temporary offers become a feature; structural rules do not share it.
- Notification/alert tables (§69) — event-sourced later off `evaluation_runs` diffs.
- Bank-relationship modelling (§61) — covered for MVP by `user_wallet_cards.fee_override` + `lifetime_free`.

Each lands as an additive migration; nothing in `0001_init.sql` needs rework to accommodate them.
