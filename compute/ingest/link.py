"""Part I SS I.4's LINK stage / SS I.9's `ingest link` tool.

Inserts sources (deduped by URL), the card/card_version/rule rows
(`status='draft'`, Part D Decision 3), and `source_links` rows
(`reviewer_status='unreviewed'`) into Postgres. Mirrors `seeds/seed.py`'s
own insertion order (card -> card_version -> caps -> earning_rules
(+cap links) -> thresholds -> tiers -> exclusions -> benefits ->
surcharges) but per-entity rather than batch, and additionally inserts
one `source_links` row per (entity, source_ref) pair, which `seed.py` has
no reason to do for its synthetic fixtures. Runs `ingest lint`'s full
battery first and refuses to touch the database at all if it fails --
"LINK never runs against a bundle LINT rejects" (I.4's own ordering).

The whole insert is one Postgres transaction (`conn.transaction()`):
either every row for this card lands, or none does. This is the FIRST
`compute/` code that writes to the catalog tables via anything other than
`seeds/seed.py`'s synthetic fixtures -- needs a reachable `DATABASE_URL`
to run or test against (unlike `ingest lint`, which needs none).

Three real prerequisites this module deliberately does NOT solve,
discovered by trying to link CASHBACK SBI's real bundle before writing
any of this code (docs/DECISIONS.md #133-#135), not assumed up front:

1. **Issuers are not created here.** `cards.issuer_id` requires an
   existing `issuers` row; `issuer_type` (bank/nbfc/network_issuer) and
   `name`/`website` are real, sourced facts Part I's own discipline (SS
   I.3: "every field traceable to a cited source") says this tool should
   never guess. `ingest link` looks the issuer up by `issuer_key` and
   refuses loudly if it isn't there -- issuer creation is a separate,
   simpler one-time step, not part of per-card linking.
2. **Currency keys are NOT namespaced by issuer in the schema**
   (`reward_currencies.key` is globally unique, confirmed against the
   live database) -- if a bundle's currency key already belongs to a
   DIFFERENT issuer, `ingest link` refuses rather than silently sharing a
   row between unrelated cards (a real card's economics must never
   depend on a row a synthetic test fixture, or another issuer's card,
   might edit later). Real bundles should use issuer-scoped currency
   keys; a currency already declared by the SAME issuer's earlier bundle
   is reused by key, per I.2's own "drafted once, referenced by key"
   text.
3. **`source_links.confidence` is a mechanical default, not I.5's full
   judgment call.** Derived purely from each cited source's own
   `source_type` (`ingest.bundle.default_confidence_for_source_type`,
   I.1's own evidentiary-weight table) -- I.5 also asks whether the
   transcription itself needed interpretation, which no bundle drafted
   so far records as a field, and which this tool has no way to infer.
   A human reviewer can still raise or lower it before publish (only
   PUBLISHED rows are immutable).
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

import psycopg

from ingest.bundle import (
    default_confidence_for_source_type,
    declared_sources,
    source_refs,
)
from ingest.lint import LintReport, lint_bundle

REQUIRED_CARD_FIELDS = ("key", "name", "network", "issuer_key", "currency", "effective_from")


class LinkError(Exception):
    """Refuses loudly and names exactly what's missing/conflicting --
    same posture Part I SS I.9 specifies for `ingest publish`, applied
    here too rather than partially inserting or silently skipping."""


@dataclass(frozen=True)
class LinkResult:
    card_key: str
    card_id: str
    card_version_id: str
    sources_inserted: int
    sources_reused: int
    source_links_inserted: int
    entity_counts: dict[str, int]
    lint_report: LintReport


def _j(x: Any) -> str:
    return json.dumps(x)


def _require(bundle: dict[str, Any]) -> None:
    missing = [f for f in REQUIRED_CARD_FIELDS if not bundle.get(f)]
    if missing:
        raise LinkError(f"bundle is missing required field(s) {missing} -- cannot LINK")


def _find_issuer_id(cur, issuer_key: str) -> Any:
    cur.execute("select id from issuers where key = %s", (issuer_key,))
    row = cur.fetchone()
    if row is None:
        raise LinkError(
            f"issuer {issuer_key!r} does not exist -- ingest link does not create issuers "
            "(name/issuer_type/website are real sourced facts this tool won't guess); "
            "insert the issuers row first, then re-run"
        )
    return row[0]


def _refuse_if_card_exists(cur, card_key: str) -> None:
    cur.execute("select id from cards where key = %s", (card_key,))
    if cur.fetchone() is not None:
        raise LinkError(
            f"card {card_key!r} already exists -- ingest link only creates the FIRST version of a "
            "new card. A rule change is a new card_versions row (Part I SS I.6's devaluation flow), "
            "not re-linking the same bundle -- that flow isn't built yet"
        )


def _resolve_currency(cur, issuer_id: Any, currency: dict[str, Any]) -> tuple[Any, bool, list[tuple[dict, Any]]]:
    """Returns (currency_id, was_newly_inserted, [(route_dict, route_id), ...for newly-inserted routes])."""
    key = currency["key"]
    cur.execute("select id, issuer_id from reward_currencies where key = %s", (key,))
    row = cur.fetchone()
    if row is not None:
        existing_id, existing_issuer_id = row
        if existing_issuer_id != issuer_id:
            raise LinkError(
                f"currency key {key!r} already belongs to a different issuer (id={existing_issuer_id}) -- "
                "reward_currencies.key is globally unique in this schema; real cards should use an "
                "issuer-scoped currency key (e.g. prefix with the issuer key) rather than sharing a row "
                "with an unrelated issuer's -- possibly synthetic-test-fixture -- card data"
            )
        return existing_id, False, []

    name = currency.get("name") or key.replace("_", " ").title()
    cur.execute(
        "insert into reward_currencies (key, name, issuer_id) values (%s,%s,%s) returning id",
        (key, name, issuer_id),
    )
    currency_id = cur.fetchone()[0]

    new_routes: list[tuple[dict, Any]] = []
    for r in currency.get("routes", []):
        cur.execute(
            "insert into redemption_routes (currency_id, key, route_type, ratio, friction_default,"
            " min_points, transfer_partner, transfer_ratio, partner_point_value)"
            " values (%s,%s,%s,%s,%s,%s,%s,%s,%s) returning id",
            (
                currency_id, r["key"], r["route_type"], r.get("ratio"),
                r.get("friction_default", 1.0), r.get("min_points"),
                r.get("transfer_partner"), r.get("transfer_ratio"), r.get("partner_point_value"),
            ),
        )
        new_routes.append((r, cur.fetchone()[0]))
    return currency_id, True, new_routes


def link_bundle(bundle: dict[str, Any], conn: psycopg.Connection) -> LinkResult:
    report = lint_bundle(bundle)
    if not report.passed:
        raise LinkError(
            f"ingest lint failed with {len(report.errors)} error(s) -- LINK never runs against a "
            "bundle LINT rejects. Run `ingest lint` directly for the full issue list."
        )
    _require(bundle)

    with conn.transaction():
        with conn.cursor() as cur:
            issuer_id = _find_issuer_id(cur, bundle["issuer_key"])
            _refuse_if_card_exists(cur, bundle["key"])

            sources = declared_sources(bundle)
            source_id_by_key: dict[str, Any] = {}
            n_inserted = n_reused = 0
            for skey, sdict in sources.items():
                cur.execute("select id from sources where url = %s", (sdict["url"],))
                row = cur.fetchone()
                if row is not None:
                    source_id_by_key[skey] = row[0]
                    n_reused += 1
                    continue
                cur.execute(
                    "insert into sources (url, source_type, issuer_id, title, captured_at,"
                    " storage_path, evidence_notes) values (%s,%s,%s,%s,%s,%s,%s) returning id",
                    (
                        sdict["url"], sdict["source_type"], issuer_id, sdict.get("title"),
                        sdict.get("captured_at"), sdict.get("storage_path"),
                        # SS I.2 spells this `evidence_notes`; the one real bundle drafted so
                        # far independently used `snapshot_note` -- same naming reconciliation
                        # already logged for `_source`/`source_refs` (ingest/bundle.py).
                        sdict.get("evidence_notes") or sdict.get("snapshot_note"),
                    ),
                )
                source_id_by_key[skey] = cur.fetchone()[0]
                n_inserted += 1

            source_links_inserted = 0

            def link_entity(entity_type: str, entity_id: Any, raw: dict[str, Any]) -> None:
                nonlocal source_links_inserted
                for ref in source_refs(raw):
                    source_id = source_id_by_key.get(ref)
                    if source_id is None:
                        raise LinkError(
                            f"{entity_type} {raw.get('key', '(unkeyed)')!r} cites source {ref!r}, "
                            "not declared in this bundle's sources block"
                        )
                    confidence = default_confidence_for_source_type(sources[ref]["source_type"])
                    cur.execute(
                        "insert into source_links (source_id, entity_type, entity_id, confidence)"
                        " values (%s,%s,%s,%s)",
                        (source_id, entity_type, entity_id, confidence),
                    )
                    source_links_inserted += 1

            currency_key = bundle["currency"]
            currency_dicts = {c["key"]: c for c in bundle.get("currencies", [])}
            if currency_key not in currency_dicts:
                raise LinkError(f"card currency {currency_key!r} has no matching entry in this bundle's currencies[]")
            currency_id, currency_is_new, new_routes = _resolve_currency(cur, issuer_id, currency_dicts[currency_key])
            if currency_is_new:
                link_entity("reward_currency", currency_id, currency_dicts[currency_key])
            for route_dict, route_id in new_routes:
                link_entity("redemption_route", route_id, route_dict)

            cur.execute(
                "insert into cards (issuer_id, key, name, network, tier, segment)"
                " values (%s,%s,%s,%s,%s,%s) returning id",
                (issuer_id, bundle["key"], bundle["name"], bundle["network"], bundle.get("tier"), bundle.get("segment")),
            )
            card_id = cur.fetchone()[0]

            v = bundle.get("version", {})
            cur.execute(
                "insert into card_versions (card_id, version_no, effective_from, joining_fee,"
                " annual_fee, gst_rate, forex_markup, currency_id)"
                " values (%s,1,%s,%s,%s,%s,%s,%s) returning id",
                (
                    card_id, bundle["effective_from"], v.get("joining_fee", 0), v.get("annual_fee", 0),
                    v.get("gst_rate", 0.18), v.get("forex_markup", 0.035), currency_id,
                ),
            )
            cv_id = cur.fetchone()[0]
            link_entity("card_version", cv_id, v)

            entity_counts: dict[str, int] = {}

            cap_ids: dict[str, Any] = {}
            for cap in bundle.get("caps", []):
                cur.execute(
                    "insert into caps (card_version_id, key, measure, amount, window_def, scope, overflow)"
                    " values (%s,%s,%s,%s,%s,%s,%s) returning id",
                    (cv_id, cap["key"], cap["measure"], cap["amount"], _j(cap["window_def"]),
                     cap.get("scope", "rule"), cap.get("overflow", "base_rate")),
                )
                cap_id = cur.fetchone()[0]
                cap_ids[cap["key"]] = cap_id
                link_entity("cap", cap_id, cap)
            entity_counts["caps"] = len(bundle.get("caps", []))

            earning_rules = bundle.get("earning_rules", [])
            for er in earning_rules:
                accrual = dict(er["accrual"])
                accrual["currency"] = currency_key  # same mechanical stamp seed.py applies
                cur.execute(
                    "insert into earning_rules (card_version_id, key, selector, accrual, rule_group,"
                    " priority, stacks_with_base, requires_activation)"
                    " values (%s,%s,%s,%s,%s,%s,%s,%s) returning id",
                    (
                        cv_id, er["key"], _j(er.get("selector", {})), _j(accrual), er.get("rule_group"),
                        er.get("priority", 10), er.get("stacks_with_base", False), er.get("requires_activation", False),
                    ),
                )
                er_id = cur.fetchone()[0]
                link_entity("earning_rule", er_id, er)
                for cap_key in er.get("caps", []):
                    cur.execute(
                        "insert into earning_rule_caps (earning_rule_id, cap_id) values (%s,%s)",
                        (er_id, cap_ids[cap_key]),
                    )
            entity_counts["earning_rules"] = len(earning_rules)

            threshold_ids: dict[str, Any] = {}
            thresholds = bundle.get("thresholds", [])
            for th in thresholds:
                cur.execute(
                    "insert into thresholds (card_version_id, key, basis, tier_mode) values (%s,%s,%s,%s) returning id",
                    (cv_id, th["key"], _j(th["basis"]), th["tier_mode"]),
                )
                th_id = cur.fetchone()[0]
                threshold_ids[th["key"]] = th_id
                link_entity("threshold", th_id, th)
                for tier in th["tiers"]:
                    cur.execute(
                        "insert into threshold_tiers (threshold_id, tier_index, threshold_amount, payload)"
                        " values (%s,%s,%s,%s)",
                        (th_id, tier["tier_index"], tier["threshold_amount"], _j(tier["payload"])),
                    )
            entity_counts["thresholds"] = len(thresholds)

            exclusions = bundle.get("exclusions", [])
            for ex in exclusions:
                cur.execute(
                    "insert into exclusions (card_version_id, key, selector, excluded_from, note)"
                    " values (%s,%s,%s,%s,%s) returning id",
                    (cv_id, ex["key"], _j(ex["selector"]), ex["excluded_from"], ex.get("note")),
                )
                ex_id = cur.fetchone()[0]
                link_entity("exclusion", ex_id, ex)
            entity_counts["exclusions"] = len(exclusions)

            benefits = bundle.get("benefits", [])
            if isinstance(benefits, dict):
                benefits = list(benefits.values())
            for b in benefits:
                qual_id = threshold_ids.get(b.get("qualification_threshold_key"))
                cur.execute(
                    "insert into benefits (card_version_id, key, kind, unit_label, entitlement,"
                    " entitlement_window, qualification_threshold_id, face_value, expiry_days,"
                    " value_ref, utilisation_ref, friction_ref)"
                    " values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) returning id",
                    (
                        cv_id, b["key"], b["kind"], b.get("unit_label"), b.get("entitlement"),
                        _j(b["entitlement_window"]) if b.get("entitlement_window") else None,
                        qual_id, b.get("face_value"), b.get("expiry_days"),
                        b.get("value_ref"), b.get("utilisation_ref"), b.get("friction_ref"),
                    ),
                )
                b_id = cur.fetchone()[0]
                link_entity("benefit", b_id, b)
            entity_counts["benefits"] = len(benefits)

            surcharges = bundle.get("surcharges", [])
            for s in surcharges:
                cur.execute(
                    "insert into surcharges (card_version_id, key, selector, rate, gst_on_surcharge, waiver)"
                    " values (%s,%s,%s,%s,%s,%s) returning id",
                    (
                        cv_id, s["key"], _j(s["selector"]), s["rate"], s.get("gst_on_surcharge", 0.18),
                        _j(s["waiver"]) if s.get("waiver") is not None else None,
                    ),
                )
                s_id = cur.fetchone()[0]
                link_entity("surcharge", s_id, s)
            entity_counts["surcharges"] = len(surcharges)

    return LinkResult(
        card_key=bundle["key"], card_id=str(card_id), card_version_id=str(cv_id),
        sources_inserted=n_inserted, sources_reused=n_reused,
        source_links_inserted=source_links_inserted, entity_counts=entity_counts,
        lint_report=report,
    )
