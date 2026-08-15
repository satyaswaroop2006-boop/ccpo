# Credit Card Portfolio Optimiser — Part I
## Real Card Ingestion Workflow

Version 0.1 · DRAFT, awaiting Satya's sign-off (no code follows until this is
approved, per CLAUDE.md's working style). Consumes Part C (the rule
vocabulary every ingested card must be expressed in) and Part D (the schema,
especially Decisions 2–4: immutability, versioning, source tracking). Closes
the gap CLAUDE.md's non-negotiable rule 4 and Part C §C.9 both point at —
"live card data enters only through Part I's verified-source workflow" — by
actually specifying that workflow, which did not exist as a document until
now.

---

# I.0 The one hard rule, stated first

**No fact about a real card's rewards, fees, or benefits may enter this
system unless it is a direct transcription from a captured, cited source.**
Not "this is roughly what I recall Card X offering." Not "this is typical for
this card tier." Not an inference from a similar card. A number with no
source citation is not data — it's a guess wearing a schema.

This binds everyone who touches ingestion, explicitly including Claude:

- Claude may capture sources, draft ingestion bundles, transcribe rule text
  into Part C's JSON vocabulary, and flag ambiguities — all useful, all
  welcome.
- Claude may **never** fill in a field from training-data recall of what a
  card "probably" offers, never mark its own draft `reviewer_status:
  approved`, and never publish a card_version. Every one of those is a
  human-only action (I.5).
- If a source doesn't state a value a rule object needs, the field is left
  unset and the bundle is flagged incomplete (I.3) — never defaulted,
  never estimated, never borrowed from a different card.

Everything below is the mechanical enforcement of this rule: a pipeline
that makes it structurally hard to violate, not just a policy that asks
nicely.

# I.1 What's admissible as a source

A source is anything that can become a `sources` row (`0001_init.sql`
§4). `source_type` is a closed vocabulary, already a DB CHECK constraint —
this document doesn't invent a new one, it just explains each value's
evidentiary weight:

| `source_type` | What it is | Weight |
|---|---|---|
| `mitc` | Most Important Terms & Conditions document | Highest — the legally binding terms |
| `fee_schedule` | Issuer's published fee schedule | Highest, for fee fields specifically |
| `official_pdf` | Any other official issuer PDF (terms, rewards catalogue) | High |
| `reward_terms` | Issuer's rewards-programme terms page | High |
| `product_page` | Issuer's marketing/product page | Medium — marketing copy tends to round and omit edge cases |
| `network_benefits` | Network (Visa/Mastercard/RuPay/Amex) benefits page | Medium, for network-level perks only |
| `transfer_partner_doc` | Airline/hotel partner's transfer-ratio page | High, for that specific route only |
| `faq` | Issuer FAQ page | Low — convenient but not authoritative |
| `third_party` | Any non-issuer, non-network source (news, review sites) | Low — never sole support for a numeric field, only for corroboration or catching what an issuer's own page omits |

**Capture, mechanically:** a source becomes a row via
`sources(url, source_type, issuer_id, title, captured_at, storage_path,
evidence_notes)` — `storage_path` points at a Supabase Storage snapshot
(a saved copy of the page/PDF as fetched, not just the live URL) taken at
`captured_at`. **A source with no snapshot is not yet captured** — a bare
URL is a lead, not evidence; issuer pages change or vanish, and every
ingested fact must be re-derivable from what was actually read, not from
whatever happens to be live today.

# I.2 The ingestion bundle — a card's draft, with provenance attached

An ingestion bundle is one JSON file, one per `card` (a card's full rule
set — all its `card_version`s, though almost always you're drafting the
current one). It extends the **implemented** card-dict shape
(`seeds/synthetic_cards.py` / `engine/card_bundle.py::bundle_from_dict`),
not Part C §C.2.10's illustrative field names verbatim — that's the shape
`bundle_from_dict` actually parses, and both the synthetic and Postgres
paths already funnel through it (CLAUDE.md rule 1's "one engine" extends
to "one loader"). The only addition: every object that maps to a
`source_links`-eligible `entity_type` carries a `source_refs: [...]` array
of source keys, and every source a bundle uses is declared once, in a
`sources` block, keyed by that same short string.

```json
{
  "issuer_key": "example_bank",
  "sources": {
    "eb_ultra_mitc_2026": {
      "url": "https://examplebank.example/cards/ultra/mitc.pdf",
      "source_type": "mitc",
      "title": "Example Bank Ultra Card — MITC",
      "captured_at": "2026-08-20",
      "storage_path": "sources/example_bank/eb_ultra_mitc_2026.pdf"
    },
    "eb_ultra_product_page_2026": {
      "url": "https://examplebank.example/cards/ultra",
      "source_type": "product_page",
      "title": "Example Bank Ultra — product page",
      "captured_at": "2026-08-20",
      "storage_path": "sources/example_bank/eb_ultra_product_page_2026.html"
    }
  },
  "key": "eb_ultra", "name": "Example Bank Ultra", "network": "visa",
  "tier": "premium", "segment": "cashback", "currency": "eb_cashback_inr",
  "version": {
    "joining_fee": 5000, "annual_fee": 5000, "forex_markup": 0.035,
    "source_refs": ["eb_ultra_mitc_2026"]
  },
  "earning_rules": [
    { "key": "base", "selector": {}, "accrual": {"type": "percentage", "rate": 0.01, "rounding": "floor_paise_per_txn"},
      "priority": 10, "source_refs": ["eb_ultra_mitc_2026"] }
  ],
  "thresholds": [
    { "key": "waiver", "basis": {"measure": "waiver_eligible_spend", "window": {"kind": "anniversary_year"}},
      "tier_mode": "cumulative",
      "tiers": [{"tier_index": 1, "threshold_amount": 300000, "payload": {"type": "waive_fee", "fee": "annual"}}],
      "source_refs": ["eb_ultra_mitc_2026"] }
  ]
}
```

(Field names elsewhere — `caps`, `exclusions`, `benefits`, `surcharges`,
`reward_currencies`/`redemption_routes` — carry `source_refs` the same
way; omitted above for brevity, not because they're exempt.)

**Granularity note, read directly off the schema, not assumed:**
`source_links.entity_type` has no `threshold_tier` value — only
`threshold`. A citation attaches at the **threshold** level, not per-tier.
If a card's ₹4L and ₹8L milestone tiers come from two different source
pages, cite both on the one threshold object (`source_refs` is a list for
exactly this reason); the DB cannot represent "tier 1 from source A, tier
2 from source B" any more finely than that. Similarly `reward_currency`
and `redemption_route` are their own citable entities — a currency's
transfer ratio to a specific airline partner needs its own citation,
separate from the card's own MITC (it usually comes from the partner's
own page, `transfer_partner_doc`), and a currency shared across several
of an issuer's cards is drafted once and referenced by key from each
card's bundle, not re-declared per card.

# I.3 Extraction discipline — how a bundle gets written, field by field

Turning source text into Part C's typed vocabulary (Selector, Accrual,
Cap, Threshold, Benefit, Surcharge) is translation, not composition. The
discipline:

1. **Every field's value must be traceable to a specific sentence or table
   cell in a cited source.** When drafting, the practice is to keep the
   source text and the JSON field side by side (a comment, a review note —
   mechanism is the drafter's choice) so a reviewer can check the
   transcription without re-deriving it.
2. **A source that doesn't state a field leaves that field absent, and
   the bundle is marked `status: "incomplete"`** (a drafting-time marker,
   not a DB column — see I.4) rather than filled with a schema default.
   Contrast with the *engine's* own registry defaults (C.7's ticket sizes,
   `upi_category_mix`) — those are explicit, disclosed, user-editable
   assumptions about spending behaviour; a missing REWARD RATE or FEE is
   not the same kind of gap and never gets the same treatment.
3. **Ambiguous wording is flagged, not resolved by best guess.** If a
   MITC says "up to 5X points on select categories" without naming the
   categories, or a cap's overflow behaviour is genuinely unstated, the
   drafter records the ambiguity in `evidence_notes` on the source (or a
   bundle-level `notes` field) and the reviewer decides — possibly by
   finding a second source, possibly by contacting the issuer, possibly
   by leaving the card in `draft` until it's resolved. It is never
   silently resolved by whichever reading makes the JSON valid.
4. **A card that genuinely cannot be expressed in Part C's vocabulary is a
   versioned engine extension, not a special case** — C.1 principle 1,
   restated here because ingestion is exactly where it gets tested against
   reality. If drafting hits this, stop and flag it the same way an
   engine-side spec gap gets flagged (CLAUDE.md: "if the spec is
   ambiguous or seems wrong, STOP and ask Satya").
5. **Numbers are transcribed at the precision the source states, never
   rounded further, never inferred from a marketing rate.** "5% cashback"
   in marketing copy vs. "5.00% subject to a ₹1,000/month cap, rounded
   down to the nearest paisa per transaction" in the MITC — the MITC's
   number and rounding rule are what gets encoded; the product page is at
   most corroboration.

# I.4 The ingestion pipeline — six stages

Mirrors C.4's staged-pipeline discipline: each stage has a clear
entry/exit condition, and nothing skips ahead.

```
CAPTURE    Fetch and snapshot the source (I.1). -> sources row exists,
           storage_path set.
DRAFT      Author or extend an ingestion bundle (I.2) against captured
           sources only, following I.3's transcription discipline.
           Exit: every field the drafter could fill IS filled; every
           field the sources don't support is left absent and the
           bundle is marked incomplete if any required field is missing.
LINT       Structural validation -- Part C SS C.11's existing battery
           (selector-overlap, threshold-payload depth, cap-scope
           resolution, currency/route completeness) PLUS one new check
           this bundle format requires: PROVENANCE COMPLETENESS -- every
           rule-bearing object (card_version fees, each earning_rule,
           cap, threshold, exclusion, benefit, surcharge, and any new
           reward_currency/redemption_route) carries a non-empty
           source_refs pointing at a source declared in the same bundle.
           A bundle failing either battery does not proceed.
LINK       Insert: sources (deduped by URL) -> card/card_version/rules
           (status='draft', per Part D Decision 3) -> source_links, one
           row per (entity, source) pair, confidence per I.5,
           reviewer_status='unreviewed'. This is the only stage that
           writes to Postgres before review.
REVIEW     A human (never the drafter alone, never an AI assistant
           unsupervised -- I.0, I.5) checks each source_link against its
           cited source and flips reviewer_status to 'approved' or
           'rejected'. A 'rejected' link blocks publish until the
           underlying field is corrected and re-drafted.
PUBLISH    Gate (I.8): the card_version may move draft -> published only
           when EVERY source_link on it (and its children) is
           'approved', it passes C.11 + provenance completeness, and it
           has >=1 passing hand-computed golden. Publishing is
           `card_versions.status='published', published_at=now()` --
           Part D Decision 2's trigger makes it irreversible without a
           new version from here on.
```

Nothing in this pipeline is new machinery at the database layer — every
table it touches (`sources`, `source_links`, `card_versions` and its
children) already exists in `0001_init.sql`. What was missing was the
*process* wrapped around them; that's what stages CAPTURE through PUBLISH
now specify.

# I.5 Confidence and reviewer_status — what the words mean

`source_links.confidence` (drafter-assigned, at LINK time):

- **`high`** — the cited source is the MITC, fee schedule, or an
  equivalent primary document, and the field is an unambiguous direct
  transcription (I.3.1).
- **`medium`** — the source is authoritative-but-secondary (product page,
  reward-terms page, official PDF) or the transcription required minor
  interpretation (e.g. resolving a table into the Selector shape).
- **`low`** — the source is a FAQ or third-party page, or the field
  required real interpretive judgement even from a primary source.
  A `low`-confidence field on a fee or reward-rate should usually block
  publish until corroborated (reviewer's call, I.4 REVIEW stage) rather
  than being waved through.

`source_links.reviewer_status` (human-assigned, at REVIEW time):

- **`unreviewed`** — default on insert. Sits in the review queue
  (`idx_slinks_review`, already indexed for exactly this).
- **`approved`** — a human other than the drafting process independently
  checked the cited source and confirms the field matches it. **Never
  set by Claude, never set by the same automated step that drafted the
  field** — self-certification isn't review (I.0).
- **`rejected`** — the field doesn't match its cited source, or the
  source itself is inadmissible/stale. Blocks publish (I.4) until fixed.

# I.6 Devaluation and version transitions

A rule change (rate cut, new cap, fee hike) is **never** an edit to a
published card_version — Part D Decision 2 makes that a database-level
impossibility, not just a convention. It's Decision 3's pattern, sourced:

1. A NEW source is captured showing the changed term (I.1) — devaluations
   are announced, and the announcement (or the updated MITC) IS the
   source.
2. A new ingestion bundle is drafted for a new `card_versions` row
   (`version_no + 1`, `status='draft'`, `effective_from` = the change's
   effective date), typically copied from the predecessor bundle and
   edited at exactly the changed fields.
3. Each changed field's new `source_links` row carries
   `previous_rule_note` describing what it superseded — the schema's own
   field for exactly this, not a free-text bolt-on.
4. The new version goes through LINT/LINK/REVIEW/PUBLISH (I.4) like any
   other. On publish, the OLD version's `effective_to` is set in the same
   transaction (Part D Decision 3) — both versions remain queryable
   forever; nothing is deleted or overwritten.

# I.7 Freshness and re-verification

`sources.last_checked_at` exists precisely because a captured source can
go stale without the underlying term actually changing notice — an issuer
quietly edits a page, a PDF link rots. Re-verification isn't specified as
a fixed cadence here (that's an operational decision for whoever runs
ingestion, not an engine-level rule) but the mechanism is: re-fetching a
source updates `last_checked_at` without creating a new `sources` row
*unless* the content actually changed, in which case it's I.6's
devaluation flow (a changed source is a new source, capturing what
changed). If two admissible sources conflict on the same field, that's an
I.3.3 ambiguity — flagged for a human, not resolved by picking the
higher-weight `source_type` automatically (a `mitc` usually wins over a
`product_page`, but "usually" is a reviewer's judgement call, not a rule
this document should encode as silent precedence).

# I.8 Golden coverage for real cards

Part C §C.11 already requires "the golden test battery of §55 plus one
golden scenario per example card" before a rule set reaches `published` —
stated for the 12 synthetic structural examples, but the *principle*
("financial correctness before visual polish… enforced mechanically") is
exactly as true for a real card, arguably more so. Extended here,
explicitly, as a PUBLISH-gate precondition (I.4): **a card_version needs
at least one hand-computed golden scenario, built the same way
`compute/goldens/golden_syn_*.json` are (a spend profile + an
independently hand-computed expected NACV, verified against
`engine.evaluate.evaluate_card`), before it is publish-eligible.** This
is the same discipline CLAUDE.md rule 2 already applies to every engine
change, applied one level down — to every *card*, not just every code
change. It catches translation bugs (a selector that matches the wrong
category, a cap converted to the wrong window) that structural linting
(I.4 LINT) cannot, the same way `golden_syn_ecom_basic.json` caught the
real `caps.py` index-shift bug (`docs/DECISIONS.md` #7) that no amount of
schema validation would have.

# I.9 Tooling shape (specified here; not built this pass)

Per Satya's direction, this document is reviewed before any
`compute/` code follows. What the eventual tooling needs to do, so the
build task (when it comes) has a clear target:

- **`ingest lint <bundle.json>`** — runs I.4's LINT stage standalone
  (structural + provenance completeness) without touching the database;
  the fast local-iteration loop for a drafter.
- **`ingest link <bundle.json>`** — runs LINT then LINK: inserts
  `sources` (deduped by URL), the card/card_version/rule rows
  (`status='draft'`), and `source_links` rows (`reviewer_status=
  'unreviewed'`). Mirrors `seeds/seed.py`'s insertion shape closely (same
  table order: card → card_version → caps → earning_rules (+cap links) →
  thresholds → tiers → exclusions → benefits → surcharges) but per-entity
  instead of batch, and it inserts `source_links` alongside every entity,
  which `seed.py` has no reason to do for synthetic fixtures.
- **`ingest review-queue`** — lists `source_links` where
  `reviewer_status='unreviewed'`, grouped by card, for whoever's
  reviewing (a thin CLI over `idx_slinks_review`, already indexed for
  this). A Part F review UI supersedes this later (I.11); the CLI is the
  MVP-scale version of the same query.
- **`ingest publish <card_version_id>`** — checks I.8's full gate (every
  child `source_link` approved, LINT still passes, >=1 passing golden
  linked) and only then flips `status='published'`. Refuses loudly,
  naming exactly which condition failed, rather than publishing partially
  or silently skipping a check — same posture as `app/main.py`'s
  `get_repository` raising loudly on a misconfigured `DATABASE_URL`
  rather than silently falling back.

None of this is speculative architecture — every piece maps to a table
or index that already exists in `0001_init.sql`. Building it is
mechanical once this document is approved; the design risk was in the
*workflow*, not the code.

# I.10 Worked example (fictional card — illustrative only)

**"Example Bank Ultra"** does not exist. Every number below is invented
for this document and must never be treated as real card data by anyone
reading it later — the point is to show the pipeline moving a card from
zero to published, not to describe an actual product.

1. **CAPTURE**: fetch `examplebank.example/cards/ultra/mitc.pdf`,
   snapshot it, insert `sources` row `eb_ultra_mitc_2026`
   (`source_type='mitc'`, `captured_at='2026-08-20'`).
2. **DRAFT**: the MITC states "1% cashback on all spend, fee ₹5,000 +GST,
   waived on ₹3,00,000 annual spend." Bundle drafted per I.2 — one
   `earning_rule` (`base`, 1% flat), one `threshold` (`waiver`, tier ₹3L →
   `waive_fee`), `version.joining_fee/annual_fee` = 5000, all four
   `source_refs: ["eb_ultra_mitc_2026"]`. The MITC says nothing about
   forex — `forex_markup` is left ABSENT (not defaulted to the engine's
   3.5% fallback), and the bundle is marked incomplete pending a forex
   source.
3. A second source is captured (`eb_ultra_product_page_2026`,
   `product_page`) which happens to state "2% forex markup" — added to
   the bundle, `forex_markup: 0.02`, `source_refs:
   ["eb_ultra_product_page_2026"]`, `confidence: "medium"` (product page,
   not MITC, per I.5).
4. **LINT**: structural checks pass; provenance completeness passes (every
   field now has a source_ref). Bundle no longer incomplete.
5. **LINK**: `cards`, `card_versions` (`status='draft'`), `earning_rules`,
   `thresholds`/`threshold_tiers` inserted; both sources inserted; three
   `source_links` rows inserted (`unreviewed`).
6. **REVIEW**: Satya opens the MITC PDF, confirms the 1% rate and ₹3L
   waiver threshold match — flips those two `source_links` to
   `approved`. Checks the product page's "2% forex" claim, isn't
   satisfied a marketing page is authoritative for a fee-like number,
   flips it to `rejected` with a note. The forex field now blocks
   publish (I.4/I.8) until a better source is found — exactly the
   ambiguity-escalation I.3.3/I.7 describe, working as intended, not a
   failure of the pipeline.
7. A `mitc`-sourced forex figure is later found and substituted; that
   `source_link` is approved. A golden scenario (I.8) is hand-computed
   for, say, ₹4,00,000/yr flat spend and checked against
   `evaluate_card`.
8. **PUBLISH**: all conditions met — `eb_ultra`'s v1 moves to
   `published`.

# I.11 Forward pointers

- **Part F** (frontend, not yet authored) will eventually give REVIEW
  (I.4) and the review queue (I.9) a proper UI — the CLI specified here
  is the MVP substitute, not a permanent design constraint.
- **Phase 5's build order**, once this document is signed off: the
  ingestion bundle JSON-Schema/dataclass (extending `engine/
  card_bundle.py`'s existing translation, not replacing it), the LINT
  battery's new provenance-completeness check alongside C.11's existing
  ones, then the `ingest link`/`ingest publish` tooling (I.9), each
  slice tested the same incremental-with-goldens way every Phase 2–4
  slice was.
- This document itself should get a `docs/DECISIONS.md` entry once
  approved, same as every other spec-level judgment call in this repo —
  logged, not just written.
