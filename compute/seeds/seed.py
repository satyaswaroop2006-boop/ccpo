"""Seed the synthetic card catalog (Part C §C.9) into the database.

Usage:
    DATABASE_URL=postgresql://... python -m seeds.seed [--draft]

Inserts issuer → currencies/routes → cards → card_versions → caps →
earning_rules (+cap links) → thresholds → tiers → exclusions → benefits →
surcharges, then publishes the versions (immutable thereafter) unless --draft.

Idempotency: refuses to run if the synthetic issuer already exists — seed into
a fresh database (local dev DBs are disposable; production fixtures change via
new versions, never edits, per Part D Decision 2).
"""
import json
import os
import sys

import psycopg

from seeds.synthetic_cards import ISSUER, CURRENCIES, CARDS

EFFECTIVE_FROM = "2026-01-01"


def j(x):
    return json.dumps(x)


def main() -> None:
    url = os.environ.get("DATABASE_URL")
    if not url:
        sys.exit("Set DATABASE_URL")
    publish = "--draft" not in sys.argv

    with psycopg.connect(url) as conn, conn.cursor() as cur:
        cur.execute("select 1 from issuers where key = %s", (ISSUER["key"],))
        if cur.fetchone():
            sys.exit("Synthetic issuer already seeded — use a fresh database.")

        cur.execute(
            "insert into issuers (key, name, issuer_type) values (%s,%s,%s) returning id",
            (ISSUER["key"], ISSUER["name"], ISSUER["issuer_type"]),
        )
        issuer_id = cur.fetchone()[0]

        currency_ids = {}
        for cu in CURRENCIES:
            cur.execute(
                "insert into reward_currencies (key, name, issuer_id) "
                "values (%s,%s,%s) returning id",
                (cu["key"], cu["name"], issuer_id),
            )
            currency_ids[cu["key"]] = cur.fetchone()[0]
            for r in cu["routes"]:
                cur.execute(
                    "insert into redemption_routes (currency_id, key, route_type, ratio,"
                    " friction_default, min_points, transfer_partner, transfer_ratio,"
                    " partner_point_value) values (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                    (
                        currency_ids[cu["key"]], r["key"], r["route_type"], r["ratio"],
                        r.get("friction_default", 1.0), r.get("min_points"),
                        r.get("transfer_partner"), r.get("transfer_ratio"),
                        r.get("partner_point_value"),
                    ),
                )

        for card in CARDS:
            cur.execute(
                "insert into cards (issuer_id, key, name, network, tier, segment)"
                " values (%s,%s,%s,%s,%s,%s) returning id",
                (issuer_id, card["key"], card["name"], card["network"],
                 card.get("tier"), card.get("segment")),
            )
            card_id = cur.fetchone()[0]

            v = card.get("version", {})
            cur.execute(
                "insert into card_versions (card_id, version_no, effective_from,"
                " joining_fee, annual_fee, forex_markup, currency_id)"
                " values (%s, 1, %s, %s, %s, %s, %s) returning id",
                (card_id, EFFECTIVE_FROM, v.get("joining_fee", 0),
                 v.get("annual_fee", 0), v.get("forex_markup", 0.035),
                 currency_ids[card["currency"]]),
            )
            cv_id = cur.fetchone()[0]

            cap_ids = {}
            for cap in card.get("caps", []):
                cur.execute(
                    "insert into caps (card_version_id, key, measure, amount,"
                    " window_def, scope, overflow) values (%s,%s,%s,%s,%s,%s,%s)"
                    " returning id",
                    (cv_id, cap["key"], cap["measure"], cap["amount"],
                     j(cap["window_def"]), cap.get("scope", "rule"),
                     cap.get("overflow", "base_rate")),
                )
                cap_ids[cap["key"]] = cur.fetchone()[0]

            for er in card.get("earning_rules", []):
                accrual = dict(er["accrual"])
                accrual["currency"] = card["currency"]
                cur.execute(
                    "insert into earning_rules (card_version_id, key, selector, accrual,"
                    " rule_group, priority, stacks_with_base, requires_activation)"
                    " values (%s,%s,%s,%s,%s,%s,%s,%s) returning id",
                    (cv_id, er["key"], j(er.get("selector", {})), j(accrual),
                     er.get("rule_group"), er.get("priority", 10),
                     er.get("stacks_with_base", False),
                     er.get("requires_activation", False)),
                )
                er_id = cur.fetchone()[0]
                for cap_key in er.get("caps", []):
                    cur.execute(
                        "insert into earning_rule_caps (earning_rule_id, cap_id)"
                        " values (%s,%s)",
                        (er_id, cap_ids[cap_key]),
                    )

            threshold_ids = {}
            for th in card.get("thresholds", []):
                cur.execute(
                    "insert into thresholds (card_version_id, key, basis, tier_mode)"
                    " values (%s,%s,%s,%s) returning id",
                    (cv_id, th["key"], j(th["basis"]), th["tier_mode"]),
                )
                threshold_ids[th["key"]] = cur.fetchone()[0]
                for tier in th["tiers"]:
                    cur.execute(
                        "insert into threshold_tiers (threshold_id, tier_index,"
                        " threshold_amount, payload) values (%s,%s,%s,%s)",
                        (threshold_ids[th["key"]], tier["tier_index"],
                         tier["threshold_amount"], j(tier["payload"])),
                    )

            for ex in card.get("exclusions", []):
                cur.execute(
                    "insert into exclusions (card_version_id, key, selector,"
                    " excluded_from, note) values (%s,%s,%s,%s,%s)",
                    (cv_id, ex["key"], j(ex["selector"]), ex["excluded_from"],
                     ex.get("note")),
                )

            for b in card.get("benefits", []):
                qual_id = threshold_ids.get(b.get("qualification_threshold_key"))
                cur.execute(
                    "insert into benefits (card_version_id, key, kind, unit_label,"
                    " entitlement, entitlement_window, qualification_threshold_id,"
                    " face_value, expiry_days, value_ref, utilisation_ref, friction_ref)"
                    " values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                    (cv_id, b["key"], b["kind"], b.get("unit_label"),
                     b.get("entitlement"),
                     j(b["entitlement_window"]) if b.get("entitlement_window") else None,
                     qual_id, b.get("face_value"), b.get("expiry_days"),
                     b.get("value_ref"), b.get("utilisation_ref"),
                     b.get("friction_ref")),
                )

            for s in card.get("surcharges", []):
                cur.execute(
                    "insert into surcharges (card_version_id, key, selector, rate,"
                    " gst_on_surcharge) values (%s,%s,%s,%s,%s)",
                    (cv_id, s["key"], j(s["selector"]), s["rate"],
                     s.get("gst_on_surcharge", 0.18)),
                )

            if publish:
                cur.execute(
                    "update card_versions set status='published', published_at=now()"
                    " where id=%s",
                    (cv_id,),
                )

        conn.commit()
        cur.execute("select count(*) from cards")
        n_cards = cur.fetchone()[0]
        cur.execute("select count(*) from earning_rules")
        n_rules = cur.fetchone()[0]
        cur.execute("select count(*) from threshold_tiers")
        n_tiers = cur.fetchone()[0]
        print(f"Seeded {n_cards} cards, {n_rules} earning rules, {n_tiers} threshold tiers"
              f" ({'published' if publish else 'draft'}).")


if __name__ == "__main__":
    main()
