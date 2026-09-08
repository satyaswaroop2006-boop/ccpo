"""Part I SS I.4's PUBLISH stage / SS I.9's `ingest publish` tool.

Checks SS I.8's full gate and only then flips `card_versions.status=
'published', published_at=now()` -- Part D Decision 2's trigger makes
that irreversible (published rows can only move to `deprecated` or gain
an `effective_to`) from this point on, a materially heavier action than
`ingest link`'s own draft insert. Refuses loudly, naming every failing
condition at once (not just the first), rather than publishing partially
or silently skipping a check -- the same posture SS I.9 specifies.

**Devaluations (SS I.6 step 4)**: if the card_version being published has
version_no > 1, publishing it ALSO closes out its immediate predecessor
in the same transaction -- the predecessor's `effective_to` is set to
one day before this version's own `effective_from`, so the two versions'
coverage never overlaps and leaves no gap. This is exactly the mutation
`0001_init.sql`'s own immutability trigger carves out as permitted on an
already-published row ("only status/effective_to may change") -- nothing
about the predecessor's rule data is touched, and it remains queryable
forever (SS I.6: "both versions remain queryable forever; nothing is
deleted or overwritten").

SS I.4's gate, restated as four checks this module actually runs:

1. **Every source_link on the card_version and its children is
   'approved'** -- PLUS the card's own `reward_currency`/`redemption_
   route` (docs/DECISIONS.md #148, resolving #141's own deliberately-left-
   open question). "Children" per Part D's table map (SS D.1:
   `card_versions` directly parents `earning_rules`/`caps`/`thresholds`/
   `exclusions`/`benefits`/`surcharges`) doesn't literally cover `reward_
   currencies`/`redemption_routes` (they hang off `issuers` as a sibling
   branch, potentially SHARED across several of an issuer's cards, SS
   I.2) -- but a card's NACV is directly a function of its currency's
   ratio being correct, the same class of risk as an unreviewed reward
   rate, and `ingest link`'s own dedup means a reused currency never
   gets a second `source_links` row: there is exactly ONE review to do
   per shared currency, ever, not repeated per card that reuses it.
   Satya confirmed: gate on it.
2. **Every one of those same source_links rows actually cites the RIGHT
   document** -- docs/DECISIONS.md #158: check 1 only ever verified
   `reviewer_status='approved'`, never that the approved row's own
   `source_id` still points at a `sources` row matching the entity's OWN
   document. The PRIME-family batch ingestion found 3 of 45 source_links
   pointing at a DIFFERENT card's document than their own bundle
   declared (2 already 'approved') -- check 1 alone would have sailed
   straight past both. `_check_source_provenance_gate` cross-references
   every entity's live `source_links.source_id` (resolved to its
   `sources.url`) against the URL the ORIGINAL BUNDLE FILE (the required
   `--bundle` argument, not the DB-reconstructed dict check 3 below
   deliberately avoids) declares for that entity's own `source_refs`/
   `_source`. Refuses loudly, naming the entity and both URLs (expected
   vs actual), same posture as every other check here. Scoped to the
   card_version and its rule-level children only -- NOT `reward_currency`/
   `redemption_route`, which `ingest link` reuses across cards without
   re-linking (see `_check_source_provenance_gate`'s own docstring for
   why checking them here would produce false positives).
3. **Passes engine-compatibility validation.** Re-run directly against
   what's actually IN THE DATABASE right now (not the original bundle
   file, which could have drifted) -- `bundle_from_dict` plus the same
   `match.validate_rule`/`eligibility.validate_exclusion`/`costs.
   validate_surcharge` checks `ingest lint` already uses. Part C SS
   C.11's own four-check battery is still unbuilt anywhere in this repo
   (confirmed by search, same as `ingest lint`'s own stated limit) --
   this is genuinely a subset of SS I.4's "C.11 + provenance
   completeness" language, not the whole thing.
4. **At least one hand-computed golden scenario passes**, evaluated
   through the REAL `engine.evaluate.evaluate_card` against what's in
   the database (SS I.8). A golden file may hold one scenario (`compute/
   goldens/golden_syn_*.json`'s own shape: `spend_annual`/`expected` at
   the top level) or several named ones (`compute/ingestion/golden_sbi_
   cashback.json`'s own shape: named keys each holding their own
   `spend_annual`/`expected`) -- both are accepted, and every scenario
   found across every `--golden` path given is evaluated and reported,
   but SS I.8 only requires ">= 1", so one passing scenario clears the
   gate even if a sibling scenario in the same file legitimately can't
   pass yet (e.g. `golden_sbi_cashback.json`'s own permanently-skipped
   EMI scenario, docs/DECISIONS.md #112 -- a known, reasoned gap, not
   something this tool should treat as a blocking failure).
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any

import psycopg

from engine.card_bundle import CardRuleBundle, bundle_from_dict, currencies_from_dicts
from engine.costs import validate_surcharge
from engine.eligibility import validate_exclusion
from engine.evaluate import EvaluateAssumptions, evaluate_card
from engine.match import validate_rule
from engine.normalise import CategorySpend, SpendInput
from ingest.bundle import declared_sources, entities_by_type_and_key, load_ingestion_bundle, source_refs

_CARD_CHILD_TABLES = {
    "earning_rule": "earning_rules",
    "cap": "caps",
    "threshold": "thresholds",
    "exclusion": "exclusions",
    "benefit": "benefits",
    "surcharge": "surcharges",
}

_EXPECTED_FIELDS = (
    ("gross_reward_value", "gross_reward_value"),
    ("milestone_value", "milestone_value"),
    ("milestone_value_year1", "milestone_value_year1"),
    ("benefit_value", "benefit_value"),
    ("fee_paid", "fee_steady"),
)

# Every `expected`/`assumptions` key this tool actually reads. A key outside
# either set (and not leading-underscore, this repo's own convention for
# documentation-only fields like `_note`/`_source`) used to be silently
# skipped -- exactly the failure mode that let golden_sbi_prime.json
# "pass" while its renamed fields (`gross_reward_value_rupees` etc.)
# checked almost nothing (docs/DECISIONS.md #152). See `_unknown_keys`.
_KNOWN_EXPECTED_KEYS = frozenset({key for key, _ in _EXPECTED_FIELDS}) | {
    "waiver_achieved", "nacv_steady_state", "nacv_year_1",
}
_KNOWN_ASSUMPTION_KEYS = frozenset({
    "primary_route", "voucher_utilisation", "voucher_friction",
    "benefit_need", "benefit_unit_value", "redemptions_per_year",
})


def _unknown_keys(d: dict[str, Any], known: frozenset[str]) -> list[str]:
    return sorted(k for k in d if k not in known and not k.startswith("_"))


class PublishError(Exception):
    """Refuses loudly, naming every failing condition -- SS I.9's own posture."""


@dataclass(frozen=True)
class ScenarioResult:
    golden_path: str
    scenario_name: str
    passed: bool
    diffs: tuple[str, ...]


@dataclass(frozen=True)
class PublishResult:
    card_key: str
    card_version_id: str
    scenario_results: tuple[ScenarioResult, ...]
    superseded_version_id: str | None = None  # Part I SS I.6 step 4, when this publish closes out a predecessor


def _j(x: Any) -> str:
    return json.dumps(x)


def entities_for_card_version(cur, card_version_id: Any) -> list[tuple[str, Any, str]]:
    """Every entity SS I.4's gate treats as "the card_version and its
    children": itself plus one row per child rule table. Does NOT include
    reward_currency/redemption_route -- those are a separate, issuer-level
    branch (see `_currency_entities_for_card_version`), not literal
    children of `card_versions` per Part D's own table map. Kept as two
    functions rather than one merged list: `ingest review-queue` groups
    currencies by ISSUER, not by card (a shared currency doesn't belong
    to one card more than another), so it needs the two families
    resolvable separately, not pre-flattened together."""
    entities: list[tuple[str, Any, str]] = [("card_version", card_version_id, "(card_version)")]
    for entity_type, table in _CARD_CHILD_TABLES.items():
        cur.execute(f"select id, key from {table} where card_version_id = %s", (card_version_id,))
        for row_id, key in cur.fetchall():
            entities.append((entity_type, row_id, key))
    return entities


def _currency_entities_for_card_version(cur, card_version_id: Any) -> list[tuple[str, Any, str]]:
    """The card_version's own currency and its routes -- resolved via
    `card_versions.currency_id`, not "every currency this issuer owns"
    (an issuer with several cards on different currencies shouldn't have
    card A's publish blocked by card B's still-unreviewed, unrelated
    currency). docs/DECISIONS.md #148."""
    cur.execute(
        "select rc.id, rc.key from card_versions cv join reward_currencies rc on rc.id = cv.currency_id"
        " where cv.id = %s",
        (card_version_id,),
    )
    currency_id, currency_key = cur.fetchone()
    entities: list[tuple[str, Any, str]] = [("reward_currency", currency_id, currency_key)]
    cur.execute("select id, key from redemption_routes where currency_id = %s", (currency_id,))
    for route_id, route_key in cur.fetchall():
        entities.append(("redemption_route", route_id, route_key))
    return entities


def _check_source_links_gate(cur, card_version_id: Any) -> list[str]:
    problems: list[str] = []
    all_entities = entities_for_card_version(cur, card_version_id) + _currency_entities_for_card_version(cur, card_version_id)
    for entity_type, entity_id, key in all_entities:
        cur.execute(
            "select reviewer_status from source_links where entity_type = %s and entity_id = %s",
            (entity_type, entity_id),
        )
        statuses = [row[0] for row in cur.fetchall()]
        if not statuses:
            problems.append(f"{entity_type} {key!r}: no source_links at all (provenance-completeness gap)")
        else:
            not_approved = sorted(s for s in statuses if s != "approved")
            if not_approved:
                problems.append(f"{entity_type} {key!r}: {len(not_approved)} source_link(s) not approved (status: {not_approved})")
    return problems


def _declared_urls_for_entity(bundle_file: dict[str, Any], entity: dict[str, Any]) -> set[str] | None:
    """URLs the ORIGINAL BUNDLE FILE declares for one entity's own
    `source_refs`/`_source`, resolved through that same file's own
    `sources`/`_sources` block. `None` when the entity cites nothing
    resolvable in this bundle -- already `ingest lint`'s own job to flag
    (an unresolvable ref, or none declared at all), not re-flagged here."""
    declared = declared_sources(bundle_file)
    urls = {declared[ref]["url"] for ref in source_refs(entity) if ref in declared}
    return urls or None


def _check_source_provenance_gate(cur, card_version_id: Any, bundle_file: dict[str, Any]) -> list[str]:
    """docs/DECISIONS.md #158: the source_links gate above only ever
    checked `reviewer_status='approved'` -- it never confirmed the
    approved row's OWN `source_id` still resolves to a `sources` row
    matching the entity's OWN document. Found during the PRIME-family
    batch ingestion: 3 of 45 source_links pointed at a DIFFERENT card's
    document than their own bundle declared (e.g. a benefit linked to an
    unrelated card's T&C booklet), 2 of them already 'approved' --
    the old gate would have sailed straight past both.

    Unlike `_check_engine_compatibility` (deliberately re-derives its
    bundle FROM the live database, to catch drift from what LINT
    originally validated), this check needs the ORIGINAL BUNDLE FILE:
    the file is the human-reviewed statement of intent ("this entity's
    evidence lives at this URL") the live `source_links` row is being
    checked AGAINST -- there is nothing in the database alone to
    cross-check that statement against.

    Deliberately scoped to `entities_for_card_version` only -- the
    card_version and its rule-level children (earning_rule/cap/threshold/
    exclusion/benefit/surcharge), i.e. exactly the entities `ingest link`
    inserts a BRAND NEW `source_links` row for on every single run, always
    citing THAT run's own bundle. `reward_currency`/`redemption_route` are
    deliberately excluded: `ingest link`'s own `_resolve_currency` reuses
    an existing currency's id (and its routes) by key without inserting
    any new `source_links` row for it (confirmed live -- caught while
    testing this very check against the devaluation flow's own currency-
    reuse fixture, docs/DECISIONS.md #157's same "only the DB's existing
    route is ever actually used" pattern) -- so a reused currency's live
    source_links legitimately still cites whichever bundle FIRST
    introduced it, not necessarily the bundle currently being published.
    Comparing that against the CURRENT bundle's own declared URL would be
    a false positive, not a real provenance mismatch. A currency/route
    provenance check would need to know which bundle originally linked
    it, which nothing in this schema records -- a real, separate gap, not
    silently pretended away by including entities this check would get
    wrong."""
    problems: list[str] = []
    bundle_entities = entities_by_type_and_key(bundle_file)
    all_entities = entities_for_card_version(cur, card_version_id)

    for entity_type, entity_id, key in all_entities:
        bundle_entity = bundle_entities.get((entity_type, key))
        if bundle_entity is None:
            problems.append(
                f"{entity_type} {key!r}: no matching entity in the given --bundle file -- wrong bundle "
                "path, or the database has an entity this bundle doesn't declare; cannot verify source "
                "provenance"
            )
            continue

        declared_urls = _declared_urls_for_entity(bundle_file, bundle_entity)
        if declared_urls is None:
            continue

        cur.execute(
            "select sl.id, s.url from source_links sl join sources s on s.id = sl.source_id"
            " where sl.entity_type = %s and sl.entity_id = %s",
            (entity_type, entity_id),
        )
        for source_link_id, live_url in cur.fetchall():
            if live_url not in declared_urls:
                problems.append(
                    f"{entity_type} {key!r}: source_links row {source_link_id} points at {live_url!r}, "
                    f"but the bundle file declares {sorted(declared_urls)!r} for this entity -- "
                    "provenance mismatch (source_id likely points at a different card/entity's document); "
                    "fix the source_links row in Supabase before retrying"
                )
    return problems


def _fetch_bundle_dict_by_version_id(conn: psycopg.Connection, card_version_id: Any) -> dict[str, Any] | None:
    """Mirrors `app/repository.py::_fetch_card_dict`'s query shape, keyed
    by card_version_id directly rather than by card_key + `current_card_
    versions` (which only exposes published rows -- exactly what PUBLISH
    must look PAST, since the row being gated is still draft when this
    runs). A separate, self-contained query rather than a shared helper
    with `app/repository.py`: the two callers have genuinely different
    status filters, and this is only the second caller of this query
    shape -- refactor into one helper if a third ever needs it, not
    before (CLAUDE.md: don't introduce abstractions beyond what's needed)."""
    with conn.cursor() as cur:
        cur.execute(
            "select c.key, c.name, c.network, c.tier, c.segment, cv.joining_fee, cv.annual_fee,"
            " cv.forex_markup, rc.key"
            " from card_versions cv join cards c on c.id = cv.card_id"
            " join reward_currencies rc on rc.id = cv.currency_id where cv.id = %s",
            (card_version_id,),
        )
        row = cur.fetchone()
        if row is None:
            return None
        (key, name, network, tier, segment, joining_fee, annual_fee, forex_markup, currency_key) = row

        cur.execute(
            "select id, key, selector, accrual, rule_group, priority, stacks_with_base, requires_activation"
            " from earning_rules where card_version_id = %s order by key",
            (card_version_id,),
        )
        rule_rows = cur.fetchall()

        cur.execute(
            "select id, key, measure, amount, window_def, scope, overflow from caps where card_version_id = %s order by key",
            (card_version_id,),
        )
        cap_rows = cur.fetchall()
        cap_key_by_id = {cap_id: cap_key for cap_id, cap_key, *_ in cap_rows}

        rule_ids = [r[0] for r in rule_rows]
        caps_by_rule_id: dict = {rid: [] for rid in rule_ids}
        if rule_ids:
            cur.execute(
                "select earning_rule_id, cap_id from earning_rule_caps where earning_rule_id = any(%s)"
                " order by earning_rule_id, cap_id",
                (rule_ids,),
            )
            for rule_id, cap_id in cur.fetchall():
                caps_by_rule_id[rule_id].append(cap_key_by_id[cap_id])

        cur.execute(
            "select id, key, basis, tier_mode from thresholds where card_version_id = %s order by key", (card_version_id,)
        )
        threshold_rows = cur.fetchall()
        threshold_key_by_id = {tid: tkey for tid, tkey, _basis, _tier_mode in threshold_rows}

        tiers_by_threshold_id: dict = {tid: [] for tid, *_ in threshold_rows}
        if threshold_rows:
            cur.execute(
                "select threshold_id, tier_index, threshold_amount, payload from threshold_tiers"
                " where threshold_id = any(%s) order by threshold_id, tier_index",
                ([tid for tid, *_ in threshold_rows],),
            )
            for tid, tier_index, threshold_amount, payload in cur.fetchall():
                tiers_by_threshold_id[tid].append(
                    {"tier_index": tier_index, "threshold_amount": threshold_amount, "payload": payload}
                )

        cur.execute(
            "select key, selector, excluded_from, note from exclusions where card_version_id = %s order by key",
            (card_version_id,),
        )
        exclusion_rows = cur.fetchall()

        cur.execute(
            "select key, kind, unit_label, entitlement, entitlement_window, qualification_threshold_id, face_value"
            " from benefits where card_version_id = %s order by key",
            (card_version_id,),
        )
        benefit_rows = cur.fetchall()

        cur.execute(
            "select key, selector, rate, gst_on_surcharge, waiver from surcharges where card_version_id = %s order by key",
            (card_version_id,),
        )
        surcharge_rows = cur.fetchall()

    earning_rules = []
    for rid, rkey, selector, accrual, rule_group, priority, stacks_with_base, requires_activation in rule_rows:
        er = {
            "key": rkey, "selector": selector or {}, "accrual": accrual, "priority": priority,
            "stacks_with_base": stacks_with_base, "requires_activation": requires_activation,
        }
        if rule_group is not None:
            er["rule_group"] = rule_group
        if caps_by_rule_id[rid]:
            er["caps"] = caps_by_rule_id[rid]
        earning_rules.append(er)

    caps = [
        {"key": ckey, "measure": measure, "amount": amount, "window_def": window_def, "scope": scope, "overflow": overflow}
        for _cid, ckey, measure, amount, window_def, scope, overflow in cap_rows
    ]

    thresholds = [
        {"key": tkey, "basis": basis, "tier_mode": tier_mode, "tiers": tiers_by_threshold_id[tid]}
        for tid, tkey, basis, tier_mode in threshold_rows
    ]

    exclusions = [
        {"key": ekey, "selector": selector, "excluded_from": list(excluded_from), "note": note}
        for ekey, selector, excluded_from, note in exclusion_rows
    ]

    benefits = []
    for bkey, kind, unit_label, entitlement, entitlement_window, qualification_threshold_id, face_value in benefit_rows:
        b = {"key": bkey, "kind": kind}
        if unit_label is not None:
            b["unit_label"] = unit_label
        if entitlement is not None:
            b["entitlement"] = entitlement
        if entitlement_window is not None:
            b["entitlement_window"] = entitlement_window
        if qualification_threshold_id is not None:
            b["qualification_threshold_key"] = threshold_key_by_id[qualification_threshold_id]
        if face_value is not None:
            b["face_value"] = face_value
        benefits.append(b)

    surcharges = []
    for skey, selector, rate, gst_on_surcharge, waiver in surcharge_rows:
        s = {"key": skey, "selector": selector, "rate": rate, "gst_on_surcharge": gst_on_surcharge}
        if waiver is not None:
            s["waiver"] = waiver
        surcharges.append(s)

    return {
        "key": key, "name": name, "network": network, "tier": tier, "segment": segment,
        "currency": currency_key,
        "version": {"joining_fee": joining_fee, "annual_fee": annual_fee, "forex_markup": forex_markup},
        "earning_rules": earning_rules, "caps": caps, "thresholds": thresholds,
        "exclusions": exclusions, "benefits": benefits, "surcharges": surcharges,
    }


def _fetch_currency_dict(conn: psycopg.Connection, card_version_id: Any) -> dict[str, Any]:
    with conn.cursor() as cur:
        cur.execute(
            "select rc.id, rc.key from card_versions cv join reward_currencies rc on rc.id = cv.currency_id"
            " where cv.id = %s",
            (card_version_id,),
        )
        currency_id, currency_key = cur.fetchone()
        cur.execute(
            "select key, route_type, ratio, friction_default, min_points, transfer_partner,"
            " transfer_ratio, partner_point_value, flat_redemption_fee from redemption_routes where currency_id = %s order by key",
            (currency_id,),
        )
        routes = []
        for rkey, route_type, ratio, friction_default, min_points, transfer_partner, transfer_ratio, partner_point_value, flat_redemption_fee in cur.fetchall():
            route: dict[str, Any] = {"key": rkey, "route_type": route_type, "friction_default": friction_default}
            if ratio is not None:
                route["ratio"] = ratio
            if min_points is not None:
                route["min_points"] = min_points
            if transfer_partner is not None:
                route["transfer_partner"] = transfer_partner
            if transfer_ratio is not None:
                route["transfer_ratio"] = transfer_ratio
            if partner_point_value is not None:
                route["partner_point_value"] = partner_point_value
            if flat_redemption_fee:
                route["flat_redemption_fee"] = flat_redemption_fee
            routes.append(route)
    return {"key": currency_key, "routes": routes}


def _check_engine_compatibility(bundle_dict: dict[str, Any]) -> list[str]:
    problems: list[str] = []
    try:
        bundle: CardRuleBundle = bundle_from_dict(bundle_dict)
    except Exception as e:
        return [f"bundle_from_dict failed on the DB's own current data: {type(e).__name__}: {e}"]

    for rule in bundle.earning_rules:
        try:
            validate_rule(rule)
        except ValueError as e:
            problems.append(f"earning_rule {rule.key!r}: {e}")
    for exclusion in bundle.exclusions:
        try:
            validate_exclusion(exclusion)
        except ValueError as e:
            problems.append(f"exclusion {exclusion.key!r}: {e}")
    for surcharge in bundle.surcharges:
        try:
            validate_surcharge(surcharge)
        except ValueError as e:
            problems.append(f"surcharge {surcharge.key!r}: {e}")
    return problems


def _spend_input_from_scenario(scenario: dict[str, Any]) -> SpendInput:
    """Same "category[/channel][~merchant_group][@geography]" key format
    tests/test_goldens.py::_parse_spend_annual uses -- a self-contained
    local copy (that function isn't production-importable; test code
    shouldn't be a dependency of `ingest/`), same posture tests/
    test_golden_sbi_cashback.py's own local `_spend_from_annual` already
    takes for the same reason."""
    lines = []
    for raw_key, amount in scenario["spend_annual"].items():
        rest, _, geography = raw_key.partition("@")
        rest, _, merchant_group = rest.partition("~")
        category, _, channel = rest.partition("/")
        lines.append(CategorySpend(
            category=category, channel=channel or None, annual_amount=Decimal(str(amount)),
            geography=geography or "domestic", merchant_group=merchant_group or None,
        ))
    return SpendInput(category_spend=tuple(lines))


def _assumptions_from_scenario(scenario: dict[str, Any]) -> EvaluateAssumptions:
    a = scenario.get("assumptions", {})
    kwargs: dict[str, Any] = {}
    if "primary_route" in a:
        kwargs["primary_routes"] = a["primary_route"]
    if "voucher_utilisation" in a:
        kwargs["voucher_utilisation"] = Decimal(str(a["voucher_utilisation"]))
    if "voucher_friction" in a:
        kwargs["voucher_friction"] = Decimal(str(a["voucher_friction"]))
    if "benefit_need" in a:
        kwargs["benefit_need"] = {k: Decimal(str(v)) for k, v in a["benefit_need"].items()}
    if "benefit_unit_value" in a:
        kwargs["benefit_unit_value"] = {k: Decimal(str(v)) for k, v in a["benefit_unit_value"].items()}
    if "redemptions_per_year" in a:
        kwargs["redemptions_per_year"] = {k: Decimal(str(v)) for k, v in a["redemptions_per_year"].items()}
    return EvaluateAssumptions(**kwargs)


def _scenarios_in_golden(golden: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    """A golden file is either ONE scenario (spend_annual/expected at the
    top level, `compute/goldens/golden_syn_*.json`'s own shape) or SEVERAL
    named ones (`compute/ingestion/golden_sbi_cashback.json`'s own shape:
    top-level keys each holding their own spend_annual/expected) -- both
    accepted, neither forced into the other's shape."""
    if "spend_annual" in golden:
        return [("(whole file)", golden)]
    return [(name, value) for name, value in golden.items() if isinstance(value, dict) and "spend_annual" in value]


def _run_scenario(bundle: CardRuleBundle, currencies: dict, golden_path: str, name: str, scenario: dict[str, Any]) -> ScenarioResult:
    expected = scenario["expected"]
    tolerance = Decimal(str(scenario.get("tolerance_rupees", "0.01")))

    # Refuse loudly on an unrecognized key BEFORE ever calling evaluate_card
    # -- same "check first, evaluate second" ordering #131's engine-
    # compatibility fix already established, and for the same reason: a
    # silently-skipped key here would make this scenario report PASS while
    # actually checking (or supplying) far less than its author intended.
    unknown_keys_by_block = {
        "assumptions": _unknown_keys(scenario.get("assumptions", {}), _KNOWN_ASSUMPTION_KEYS),
        "expected": _unknown_keys(expected, _KNOWN_EXPECTED_KEYS),
    }
    key_diffs = [
        f"unrecognized {block} key(s) {keys} -- typo, or a field this tool doesn't read yet; "
        f"prefix with '_' if intentionally informational (this repo's _note/_source convention)"
        for block, keys in unknown_keys_by_block.items() if keys
    ]
    if key_diffs:
        return ScenarioResult(golden_path=golden_path, scenario_name=name, passed=False, diffs=tuple(key_diffs))

    spend = _spend_input_from_scenario(scenario)
    assumptions = _assumptions_from_scenario(scenario)
    result = evaluate_card(bundle, currencies, spend, assumptions)
    actual_by_field = {
        "gross_reward_value": result.gross_reward_value, "milestone_value": result.milestone_value,
        "milestone_value_year1": result.milestone_value_year1, "benefit_value": result.benefit_value,
        "fee_paid": result.fee_steady,
    }

    diffs = []
    for expected_key, _actual_key in _EXPECTED_FIELDS:
        if expected_key not in expected:
            continue
        want = Decimal(str(expected[expected_key]))
        got = actual_by_field[expected_key]
        if abs(got - want) > tolerance:
            diffs.append(f"{expected_key}: expected {want}, got {got} (tolerance {tolerance})")
    if "waiver_achieved" in expected and result.waiver_achieved != expected["waiver_achieved"]:
        diffs.append(f"waiver_achieved: expected {expected['waiver_achieved']}, got {result.waiver_achieved}")
    if "nacv_steady_state" in expected:
        want = Decimal(str(expected["nacv_steady_state"]))
        if abs(result.nacv.steady_state - want) > tolerance:
            diffs.append(f"nacv_steady_state: expected {want}, got {result.nacv.steady_state} (tolerance {tolerance})")
    if "nacv_year_1" in expected:
        want = Decimal(str(expected["nacv_year_1"]))
        if abs(result.nacv.year_1 - want) > tolerance:
            diffs.append(f"nacv_year_1: expected {want}, got {result.nacv.year_1} (tolerance {tolerance})")

    return ScenarioResult(golden_path=golden_path, scenario_name=name, passed=not diffs, diffs=tuple(diffs))


def publish_card_version(conn: psycopg.Connection, card_version_id: Any, golden_paths: list[str], bundle_path: str) -> PublishResult:
    with conn.cursor() as cur:
        cur.execute(
            "select c.key, cv.status from card_versions cv join cards c on c.id = cv.card_id where cv.id = %s",
            (card_version_id,),
        )
        row = cur.fetchone()
        if row is None:
            raise PublishError(f"card_version {card_version_id} does not exist")
        card_key, status = row
        if status != "draft":
            raise PublishError(f"card_version {card_version_id} (card {card_key!r}) has status {status!r}, not 'draft' -- nothing to publish")

        problems = _check_source_links_gate(cur, card_version_id)

        try:
            bundle_file = load_ingestion_bundle(bundle_path)
        except (OSError, ValueError) as e:
            problems.append(f"--bundle {bundle_path!r} could not be loaded: {type(e).__name__}: {e} -- source-provenance check skipped")
        else:
            problems.extend(_check_source_provenance_gate(cur, card_version_id, bundle_file))

    bundle_dict = _fetch_bundle_dict_by_version_id(conn, card_version_id)
    problems.extend(_check_engine_compatibility(bundle_dict))

    if not golden_paths:
        problems.append("no --golden path given -- Part I SS I.8 requires at least one hand-computed golden scenario before publish")

    if problems:
        # Refuse BEFORE ever calling evaluate_card: a bundle that fails
        # engine-compatibility isn't safe to hand to the evaluator at all
        # (match_segment's own validate_rule call raises an uncaught
        # ValueError deep inside evaluate_card rather than degrading
        # gracefully -- confirmed by a failing test written against this
        # exact scenario before this ordering fix, not assumed).
        detail = "\n  - ".join(problems)
        raise PublishError(f"publish gate failed for card_version {card_version_id} (card {card_key!r}):\n  - {detail}")

    bundle = bundle_from_dict(bundle_dict)
    currencies = currencies_from_dicts([_fetch_currency_dict(conn, card_version_id)])

    scenario_results: list[ScenarioResult] = []
    for path in golden_paths:
        golden = json.loads(Path(path).read_text())
        scenarios = _scenarios_in_golden(golden)
        if not scenarios:
            problems.append(f"{path}: no scenarios found (expected spend_annual/expected, at the top level or nested)")
            continue
        for name, scenario in scenarios:
            scenario_results.append(_run_scenario(bundle, currencies, path, name, scenario))

    if not scenario_results or not any(r.passed for r in scenario_results):
        problems.append(
            f"no passing golden scenario found across {len(scenario_results)} scenario(s) checked "
            "(Part I SS I.8 requires >= 1)"
        )

    if problems:
        detail = "\n  - ".join(problems)
        raise PublishError(f"publish gate failed for card_version {card_version_id} (card {card_key!r}):\n  - {detail}")

    superseded_id = None
    with conn.transaction():
        with conn.cursor() as cur:
            cur.execute(
                "select card_id, version_no, effective_from from card_versions where id = %s",
                (card_version_id,),
            )
            this_card_id, this_version_no, this_effective_from = cur.fetchone()

            cur.execute(
                "select id from card_versions where card_id = %s and status = 'published' and version_no < %s"
                " order by version_no desc limit 1",
                (this_card_id, this_version_no),
            )
            predecessor = cur.fetchone()

            cur.execute(
                "update card_versions set status = 'published', published_at = now() where id = %s",
                (card_version_id,),
            )

            if predecessor is not None:
                superseded_id = str(predecessor[0])
                cur.execute(
                    "update card_versions set effective_to = %s::date - interval '1 day' where id = %s",
                    (this_effective_from, predecessor[0]),
                )

    return PublishResult(
        card_key=card_key, card_version_id=str(card_version_id), scenario_results=tuple(scenario_results),
        superseded_version_id=superseded_id,
    )
