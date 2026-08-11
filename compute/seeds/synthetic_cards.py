"""Synthetic card catalog — the 12 structural test cards of Part C §C.9.

These are DELIBERATELY SYNTHETIC. They exercise every construct in the rules
engine but carry no claim of matching any real Indian card. Real card data
enters only through the Part I verified-source ingestion workflow.

Structure mirrors the Part D tables. The seed loader (seed.py) inserts in
dependency order and publishes the versions (immutable thereafter — reseed a
fresh DB to change fixtures, exactly as production changes require a new
version).
"""

ISSUER = {"key": "synthetic_bank", "name": "Synthetic Bank", "issuer_type": "bank"}

CURRENCIES = [
    {"key": "cashback_inr", "name": "Cashback INR",
     "routes": [
         {"key": "stmt", "route_type": "statement_credit", "ratio": 1.0},
     ]},
    {"key": "synth_points", "name": "Synth Points",
     "routes": [
         {"key": "stmt",    "route_type": "statement_credit", "ratio": 0.25},
         {"key": "voucher", "route_type": "voucher",          "ratio": 0.35},
         {"key": "portal",  "route_type": "travel_portal",    "ratio": 0.50, "friction_default": 0.9},
         {"key": "transfer","route_type": "transfer",         "ratio": 1.00, "friction_default": 0.8,
          "transfer_partner": "synth_air", "transfer_ratio": 1.0, "partner_point_value": 1.0,
          "min_points": 5000},
     ]},
]

def pct(rate): return {"type": "percentage", "rate": rate, "rounding": "floor_paise_per_txn", "currency": None}
def per_unit(unit, pts): return {"type": "per_unit", "unit_amount": unit, "points_per_unit": pts,
                                 "rounding": "floor_per_txn", "currency": None}
MONTH = {"kind": "calendar_month"}
QTR   = {"kind": "quarter", "alignment": "calendar"}
ANNIV = {"kind": "anniversary_year"}

CARDS = [
# ── 1. Flat uncapped cashback — baseline sanity ─────────────────────────────
{"key": "syn_flat", "name": "Synth Flat 1.5", "network": "visa", "tier": "entry",
 "segment": "cashback", "currency": "cashback_inr",
 "version": {"annual_fee": 0, "joining_fee": 0, "forex_markup": 0.035},
 "earning_rules": [
     {"key": "base", "selector": {}, "accrual": pct(0.015), "priority": 10}]},

# ── 2. Capped accelerated ecommerce cashback (C.9 Ex.2) ─────────────────────
{"key": "syn_ecom", "name": "Synth Ecom 5", "network": "visa", "tier": "core",
 "segment": "cashback", "currency": "cashback_inr",
 "version": {"annual_fee": 500, "joining_fee": 500},
 "earning_rules": [
     {"key": "base", "selector": {}, "accrual": pct(0.01), "priority": 10},
     {"key": "ecom", "selector": {"categories": ["ecommerce"], "channels": ["online"]},
      "accrual": pct(0.05), "priority": 100, "caps": ["cap_ecom"]}],
 "caps": [
     {"key": "cap_ecom", "measure": "reward", "amount": 1000,
      "window_def": MONTH, "scope": "rule", "overflow": "base_rate"}],
 "thresholds": [
     {"key": "waiver", "basis": {"measure": "waiver_eligible_spend", "window": ANNIV},
      "tier_mode": "cumulative",
      "tiers": [{"tier_index": 1, "threshold_amount": 100000,
                 "payload": {"type": "waive_fee", "fee": "annual"}}]}]},

# ── 3. Points card, stacking bonus, shared group cap (C.9 Ex.3) ─────────────
{"key": "syn_points", "name": "Synth Points Portal", "network": "visa", "tier": "premium",
 "segment": "rewards", "currency": "synth_points",
 "version": {"annual_fee": 2500, "joining_fee": 2500},
 "earning_rules": [
     {"key": "base", "selector": {}, "accrual": per_unit(150, 5), "priority": 10},
     {"key": "portal_bonus", "selector": {"merchant_groups": ["synth_portal"]},
      "accrual": per_unit(150, 20), "priority": 100, "stacks_with_base": True,
      "rule_group": "portal_accel", "caps": ["cap_portal"]}],
 "caps": [
     {"key": "cap_portal", "measure": "reward", "amount": 15000,
      "window_def": MONTH, "scope": "rule_group:portal_accel", "overflow": "zero"}],
 "thresholds": [
     {"key": "waiver", "basis": {"measure": "waiver_eligible_spend", "window": ANNIV},
      "tier_mode": "cumulative",
      "tiers": [{"tier_index": 1, "threshold_amount": 300000,
                 "payload": {"type": "waive_fee", "fee": "annual"}}]}]},

# ── 4. Cumulative annual milestone vouchers (C.9 Ex.4) ──────────────────────
{"key": "syn_miles", "name": "Synth Milestone Elite", "network": "mastercard",
 "tier": "premium", "segment": "travel", "currency": "synth_points",
 "version": {"annual_fee": 10000, "joining_fee": 10000},
 "earning_rules": [
     {"key": "base", "selector": {}, "accrual": per_unit(200, 4), "priority": 10}],
 "benefits": [
     {"key": "vch_a", "kind": "voucher", "face_value": 10000, "expiry_days": 365,
      "friction_ref": "assump.voucher_friction", "utilisation_ref": "user.voucher_util"},
     {"key": "vch_b", "kind": "voucher", "face_value": 10000, "expiry_days": 365,
      "friction_ref": "assump.voucher_friction", "utilisation_ref": "user.voucher_util"}],
 "thresholds": [
     {"key": "annual_miles", "basis": {"measure": "milestone_eligible_spend", "window": ANNIV},
      "tier_mode": "cumulative",
      "tiers": [
          {"tier_index": 1, "threshold_amount": 400000,
           "payload": {"type": "grant_voucher", "benefit": "vch_a"}},
          {"tier_index": 2, "threshold_amount": 800000,
           "payload": {"type": "grant_voucher", "benefit": "vch_b"}}]}]},

# ── 5. Fee waiver + scoped exclusions (C.9 Ex.5) ────────────────────────────
{"key": "syn_waiver", "name": "Synth Waiver One", "network": "visa", "tier": "entry",
 "segment": "cashback", "currency": "cashback_inr",
 "version": {"annual_fee": 999, "joining_fee": 999},
 "earning_rules": [
     {"key": "base", "selector": {}, "accrual": pct(0.01), "priority": 10}],
 "exclusions": [
     {"key": "rent_no_waiver", "selector": {"categories": ["rent"]},
      "excluded_from": ["fee_waiver"],
      "note": "Rent earns rewards here but does NOT count toward the waiver"},
     {"key": "fuel_no_rewards", "selector": {"categories": ["fuel"]},
      "excluded_from": ["rewards"],
      "note": "Fuel earns nothing but DOES count toward the waiver"}],
 "thresholds": [
     {"key": "waiver", "basis": {"measure": "waiver_eligible_spend", "window": ANNIV},
      "tier_mode": "cumulative",
      "tiers": [{"tier_index": 1, "threshold_amount": 300000,
                 "payload": {"type": "waive_fee", "fee": "annual"}}]}]},

# ── 6. Retroactive highest-only tiers via rate activation (C.9 Ex.6) ────────
{"key": "syn_retro", "name": "Synth Retro Tiers", "network": "visa", "tier": "core",
 "segment": "cashback", "currency": "cashback_inr",
 "version": {"annual_fee": 0},
 "earning_rules": [
     {"key": "base", "selector": {}, "accrual": pct(0.01), "priority": 10},
     {"key": "rate_2", "selector": {}, "accrual": pct(0.02), "priority": 50,
      "requires_activation": True},
     {"key": "rate_3", "selector": {}, "accrual": pct(0.03), "priority": 60,
      "requires_activation": True}],
 "thresholds": [
     {"key": "tiers", "basis": {"measure": "milestone_eligible_spend", "window": ANNIV},
      "tier_mode": "highest_only",
      "tiers": [
          {"tier_index": 1, "threshold_amount": 100000,
           "payload": {"type": "activate_rule", "rule": "rate_2", "application": "retroactive"}},
          {"tier_index": 2, "threshold_amount": 300000,
           "payload": {"type": "activate_rule", "rule": "rate_3", "application": "retroactive"}}]}]},

# ── 7. Incremental slabs via spend-measure caps (C.9 Ex.7) ──────────────────
{"key": "syn_slab", "name": "Synth Slab Up", "network": "mastercard", "tier": "core",
 "segment": "cashback", "currency": "cashback_inr",
 "version": {"annual_fee": 0},
 # NOTE for optimiser: rule_group 'slab' with INCREASING rates ⇒ convex PWL ⇒
 # fill-order binaries required (Part B §B.5 / Part E §E.4).
 "earning_rules": [
     {"key": "slab1", "selector": {}, "accrual": pct(0.01), "priority": 30,
      "rule_group": "slab", "caps": ["band1"]},
     {"key": "slab2", "selector": {}, "accrual": pct(0.02), "priority": 20,
      "rule_group": "slab", "caps": ["band2"]},
     {"key": "slab3", "selector": {}, "accrual": pct(0.03), "priority": 10,
      "rule_group": "slab"}],
 "caps": [
     {"key": "band1", "measure": "spend", "amount": 100000,
      "window_def": ANNIV, "scope": "rule", "overflow": "zero"},
     {"key": "band2", "measure": "spend", "amount": 200000,
      "window_def": ANNIV, "scope": "rule", "overflow": "zero"}]},

# ── 8. Zero-forex travel card (C.9 Ex.8) ────────────────────────────────────
{"key": "syn_travel", "name": "Synth Zero FX", "network": "visa", "tier": "premium",
 "segment": "travel", "currency": "synth_points",
 "version": {"annual_fee": 3000, "joining_fee": 3000, "forex_markup": 0.0},
 "earning_rules": [
     {"key": "base", "selector": {}, "accrual": per_unit(100, 1), "priority": 10},
     {"key": "intl", "selector": {"geography": "international"},
      "accrual": per_unit(100, 2), "priority": 100}],
 "thresholds": [
     {"key": "waiver", "basis": {"measure": "waiver_eligible_spend", "window": ANNIV},
      "tier_mode": "cumulative",
      "tiers": [{"tier_index": 1, "threshold_amount": 250000,
                 "payload": {"type": "waive_fee", "fee": "annual"}}]}]},

# ── 9. RuPay UPI card, channel rules + channel×category exclusion (Ex.9) ────
{"key": "syn_upi", "name": "Synth UPI Ru", "network": "rupay", "tier": "entry",
 "segment": "upi", "currency": "synth_points",
 "version": {"annual_fee": 0},
 "earning_rules": [
     {"key": "upi", "selector": {"channels": ["upi"]},
      "accrual": per_unit(100, 1), "priority": 100, "caps": ["cap_upi"]}],
 "caps": [
     {"key": "cap_upi", "measure": "reward", "amount": 500,
      "window_def": MONTH, "scope": "rule", "overflow": "zero"}],
 "exclusions": [
     {"key": "upi_fuel_rent", "selector": {"channels": ["upi"], "categories": ["fuel", "rent"]},
      "excluded_from": ["rewards"],
      "note": "UPI fuel/rent earns nothing"}]},

# ── 10. Fuel card: surcharge + capped refund rule (C.9 Ex.10) ───────────────
{"key": "syn_fuel", "name": "Synth Fuel Saver", "network": "rupay", "tier": "entry",
 "segment": "fuel", "currency": "cashback_inr",
 "version": {"annual_fee": 500},
 "earning_rules": [
     {"key": "base", "selector": {}, "accrual": pct(0.005), "priority": 10},
     {"key": "fuel_refund", "selector": {"categories": ["fuel"]},
      "accrual": pct(0.01), "priority": 100, "stacks_with_base": True,
      "caps": ["cap_refund"]}],
 "caps": [
     {"key": "cap_refund", "measure": "reward", "amount": 250,
      "window_def": MONTH, "scope": "rule", "overflow": "zero"}],
 "surcharges": [
     {"key": "fuel_sur", "selector": {"categories": ["fuel"]},
      "rate": 0.01, "gst_on_surcharge": 0.18}],
 "thresholds": [
     {"key": "waiver", "basis": {"measure": "waiver_eligible_spend", "window": ANNIV},
      "tier_mode": "cumulative",
      "tiers": [{"tier_index": 1, "threshold_amount": 50000,
                 "payload": {"type": "waive_fee", "fee": "annual"}}]}]},

# ── 11. Quarterly-gated lounge quota (C.9 Ex.11) ────────────────────────────
{"key": "syn_lounge", "name": "Synth Lounge Q", "network": "mastercard",
 "tier": "premium", "segment": "travel", "currency": "synth_points",
 "version": {"annual_fee": 5000, "joining_fee": 5000},
 "earning_rules": [
     {"key": "base", "selector": {}, "accrual": per_unit(150, 2), "priority": 10}],
 "thresholds": [
     {"key": "q_spend", "basis": {"measure": "milestone_eligible_spend", "window": QTR},
      "tier_mode": "cumulative",
      "tiers": [{"tier_index": 1, "threshold_amount": 75000,
                 "payload": {"type": "grant_entitlement", "benefit": "dom_lounge",
                             "quantity": 4, "window": QTR}}]}],
 "benefits": [
     {"key": "dom_lounge", "kind": "countable", "unit_label": "domestic lounge visit",
      "entitlement": 4, "entitlement_window": QTR,
      "qualification_threshold_key": "q_spend",
      "value_ref": "assump.lounge_domestic_value",
      "utilisation_ref": "user.lounge_need"}]},

# ── 12. Renewal benefit + prospective rate unlock (C.9 Ex.12) ───────────────
{"key": "syn_renewal", "name": "Synth Renewal Plus", "network": "visa", "tier": "core",
 "segment": "rewards", "currency": "synth_points",
 "version": {"annual_fee": 1500, "joining_fee": 1500},
 "earning_rules": [
     {"key": "base", "selector": {}, "accrual": per_unit(100, 1), "priority": 10},
     {"key": "dining_2x", "selector": {"categories": ["dining"]},
      "accrual": per_unit(100, 2), "priority": 100, "requires_activation": True}],
 "thresholds": [
     {"key": "anniv", "basis": {"measure": "milestone_eligible_spend", "window": ANNIV},
      "tier_mode": "cumulative",
      "tiers": [
          {"tier_index": 1, "threshold_amount": 100000,
           "payload": {"type": "activate_rule", "rule": "dining_2x",
                       "application": "prospective"}},
          {"tier_index": 2, "threshold_amount": 500000,
           "payload": {"type": "grant_points", "amount": 10000,
                       "currency": "synth_points", "condition": "on_renewal"}}]}]},
]
