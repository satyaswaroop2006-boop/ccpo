"""Card data access (Part E SS E.0's `/evaluate` input boundary).

`CardRepository` is the seam between the API layer and wherever card rule
data actually lives, so `app/main.py`'s handlers never care which one is
behind it. Two implementations:

- `SyntheticCatalogRepository` -- backed by `seeds/synthetic_cards.py`,
  the same fixtures the golden battery uses. Always available, no network.
- `PostgresCardRepository` -- reads `card_versions`/`earning_rules`/
  `caps`/`earning_rule_caps`/`thresholds`/`threshold_tiers`/`exclusions`/
  `benefits`/`surcharges`/`reward_currencies`/`redemption_routes` per
  `supabase/migrations/0001_init.sql`, assembling each card into the exact
  same dict shape `seeds/synthetic_cards.py`'s `CARDS` entries already
  have (verified column-by-column against a live seeded database, see
  docs/DECISIONS.md's Phase 3 follow-up entry) and feeding it through the
  SAME `engine/card_bundle.bundle_from_dict` translation
  `SyntheticCatalogRepository` uses -- both data sources funnel through
  one translation so they can't silently diverge in interpretation.
  Resolves current-version selection via the `current_card_versions` view
  (published, effective today) rather than reimplementing that logic.
"""
from __future__ import annotations

from decimal import Decimal
from typing import Protocol

import psycopg

from engine.card_bundle import CardRuleBundle, bundle_from_dict, currencies_from_dicts
from engine.valuation import RewardCurrency
from seeds.synthetic_cards import CARDS, CURRENCIES


class CardNotFoundError(KeyError):
    pass


class CardRepository(Protocol):
    def get_card_bundle(self, card_key: str) -> CardRuleBundle: ...

    def get_currencies(self) -> dict[str, RewardCurrency]: ...

    def get_all_card_bundles(self) -> list[CardRuleBundle]: ...


class SyntheticCatalogRepository:
    """Backed by `seeds/synthetic_cards.py` -- the 12 structural test cards
    of Part C SS C.9, same fixtures the golden battery runs against. Not a
    stand-in for real card data (CLAUDE.md rule 4: never source real
    reward data from memory); this is purely what makes `/evaluate` and
    `/next-best-spend` runnable and testable without a database."""

    def __init__(self) -> None:
        self._cards_by_key = {c["key"]: c for c in CARDS}
        self._currencies = currencies_from_dicts(CURRENCIES)

    def get_card_bundle(self, card_key: str) -> CardRuleBundle:
        card = self._cards_by_key.get(card_key)
        if card is None:
            raise CardNotFoundError(card_key)
        return bundle_from_dict(card)

    def get_currencies(self) -> dict[str, RewardCurrency]:
        return self._currencies

    def get_all_card_bundles(self) -> list[CardRuleBundle]:
        """Part E SS E.2's candidate-selection input: "live card universe."
        Every card in the fixture set, in seed order -- `optimiser/
        candidates.py::select_candidates` does its own ranking, so no
        ordering guarantee is needed here beyond determinism."""
        return [bundle_from_dict(c) for c in CARDS]


class PostgresCardRepository:
    """Reads the live catalog. Opens one connection at construction and
    keeps it for the repository's lifetime (matches this service's
    request-scoped-instance-per-call usage, per `app/main.py`'s
    `lru_cache`d factory); call `.close()` on shutdown."""

    def __init__(self, dsn: str) -> None:
        self._conn = psycopg.connect(dsn)

    def close(self) -> None:
        self._conn.close()

    def get_card_bundle(self, card_key: str) -> CardRuleBundle:
        card = _fetch_card_dict(self._conn, card_key)
        if card is None:
            raise CardNotFoundError(card_key)
        return bundle_from_dict(card)

    def get_currencies(self) -> dict[str, RewardCurrency]:
        return currencies_from_dicts(_fetch_currency_dicts(self._conn))

    def get_all_card_bundles(self) -> list[CardRuleBundle]:
        """One query for the live key list, then `_fetch_card_dict` per key
        (N+1, same per-card query shape `get_card_bundle` already uses) --
        simplest correct option at today's catalog size; a single wide
        query is a performance follow-up, not a correctness concern."""
        keys = _fetch_all_card_keys(self._conn)
        return [bundle_from_dict(_fetch_card_dict(self._conn, key)) for key in keys]


def _fetch_all_card_keys(conn: psycopg.Connection) -> list[str]:
    with conn.cursor() as cur:
        cur.execute(
            "select c.key from cards c join current_card_versions cv on cv.card_id = c.id order by c.key"
        )
        return [row[0] for row in cur.fetchall()]


def _fetch_card_dict(conn: psycopg.Connection, card_key: str) -> dict | None:
    with conn.cursor() as cur:
        cur.execute(
            "select c.id, c.key, c.name, c.network, c.tier, c.segment, cv.id, cv.joining_fee,"
            " cv.annual_fee, cv.forex_markup, rc.key"
            " from cards c"
            " join current_card_versions cv on cv.card_id = c.id"
            " join reward_currencies rc on rc.id = cv.currency_id"
            " where c.key = %s",
            (card_key,),
        )
        row = cur.fetchone()
        if row is None:
            return None
        (_card_id, key, name, network, tier, segment, cv_id, joining_fee, annual_fee, forex_markup, currency_key) = row

        cur.execute(
            "select id, key, selector, accrual, rule_group, priority, stacks_with_base, requires_activation"
            " from earning_rules where card_version_id = %s order by key",
            (cv_id,),
        )
        rule_rows = cur.fetchall()

        cur.execute(
            "select id, key, measure, amount, window_def, scope, overflow from caps where card_version_id = %s"
            " order by key",
            (cv_id,),
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
            "select id, key, basis, tier_mode from thresholds where card_version_id = %s order by key", (cv_id,)
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
            (cv_id,),
        )
        exclusion_rows = cur.fetchall()

        cur.execute(
            "select key, kind, unit_label, entitlement, entitlement_window, qualification_threshold_id, face_value"
            " from benefits where card_version_id = %s order by key",
            (cv_id,),
        )
        benefit_rows = cur.fetchall()

        cur.execute(
            "select key, selector, rate, gst_on_surcharge from surcharges where card_version_id = %s order by key",
            (cv_id,),
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

    surcharges = [
        {"key": skey, "selector": selector, "rate": rate, "gst_on_surcharge": gst_on_surcharge}
        for skey, selector, rate, gst_on_surcharge in surcharge_rows
    ]

    return {
        "key": key, "name": name, "network": network, "tier": tier, "segment": segment,
        "currency": currency_key,
        "version": {"joining_fee": joining_fee, "annual_fee": annual_fee, "forex_markup": forex_markup},
        "earning_rules": earning_rules,
        "caps": caps,
        "thresholds": thresholds,
        "exclusions": exclusions,
        "benefits": benefits,
        "surcharges": surcharges,
    }


def _fetch_currency_dicts(conn: psycopg.Connection) -> list[dict]:
    with conn.cursor() as cur:
        cur.execute("select id, key from reward_currencies order by key")
        currency_rows = cur.fetchall()
        cur.execute(
            "select currency_id, key, route_type, ratio, friction_default, min_points,"
            " transfer_partner, transfer_ratio, partner_point_value from redemption_routes"
            " order by currency_id, key"
        )
        route_rows = cur.fetchall()

    routes_by_currency_id: dict = {cid: [] for cid, _key in currency_rows}
    for currency_id, rkey, route_type, ratio, friction_default, min_points, transfer_partner, transfer_ratio, partner_point_value in route_rows:
        route: dict = {"key": rkey, "route_type": route_type, "friction_default": friction_default}
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
        routes_by_currency_id[currency_id].append(route)

    return [{"key": key, "routes": routes_by_currency_id[cid]} for cid, key in currency_rows]
