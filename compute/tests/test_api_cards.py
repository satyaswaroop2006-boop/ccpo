"""FastAPI TestClient tests for GET /cards and GET /cards/{card_key} --
Part F §F.2.1's Catalog screen, Slice 1 (F.8). Backed by
SyntheticCatalogRepository, same pinning convention as
tests/test_api_evaluate.py (docs/DECISIONS.md #67) -- fast, deterministic,
no DATABASE_URL needed. These check the HTTP/JSON shape and the summary/
detail split (Part F §F.2.1: display metadata `CardRuleBundle` itself
doesn't carry, plus a direct dump of the rule vocabulary), not engine
correctness -- that's `tests/test_goldens.py`'s job, unchanged by this file.
"""
from decimal import Decimal

from fastapi.testclient import TestClient

from app.main import app, get_repository
from app.repository import SyntheticCatalogRepository
from seeds.synthetic_cards import CARDS, ISSUER

app.dependency_overrides[get_repository] = SyntheticCatalogRepository
client = TestClient(app)


def test_list_cards_returns_every_synthetic_card_once():
    response = client.get("/cards")
    assert response.status_code == 200
    body = response.json()
    assert len(body) == len(CARDS)
    assert {c["card_key"] for c in body} == {c["key"] for c in CARDS}


def test_list_cards_summary_fields_match_the_source_dict():
    """syn_flat is the simplest fixture (one flat base rule, no caps/
    thresholds/exclusions/benefits/surcharges) -- good for pinning the
    summary's own fields exactly, independent of rule-breakdown noise."""
    response = client.get("/cards")
    assert response.status_code == 200
    by_key = {c["card_key"]: c for c in response.json()}
    syn_flat = by_key["syn_flat"]

    assert syn_flat["name"] == "Synth Flat 1.5"
    assert syn_flat["issuer_name"] == ISSUER["name"]
    assert syn_flat["network"] == "visa"
    assert syn_flat["tier"] == "entry"
    assert syn_flat["segment"] == "cashback"
    assert Decimal(syn_flat["joining_fee"]) == Decimal("0")
    assert Decimal(syn_flat["annual_fee"]) == Decimal("0")
    assert syn_flat["currency_key"] == "cashback_inr"


def test_get_card_unknown_key_returns_404():
    response = client.get("/cards/not_a_real_card")
    assert response.status_code == 404


def test_get_card_detail_includes_summary_and_rule_breakdown():
    response = client.get("/cards/syn_flat")
    assert response.status_code == 200
    body = response.json()

    # Summary fields present (CardDetailOut extends CardSummaryOut).
    assert body["card_key"] == "syn_flat"
    assert body["name"] == "Synth Flat 1.5"

    # Rule breakdown -- syn_flat has exactly one earning_rule, nothing else.
    assert len(body["earning_rules"]) == 1
    rule = body["earning_rules"][0]
    assert rule["key"] == "base"
    assert rule["accrual"]["type"] == "percentage"
    assert Decimal(rule["accrual"]["rate"]) == Decimal("0.015")
    assert body["caps"] == []
    assert body["thresholds"] == []
    assert body["exclusions"] == []
    assert body["benefits"] == []
    assert body["surcharges"] == []


def test_get_card_detail_serializes_nested_structures_for_a_richer_card():
    """syn_ecom has a cap (nested Window) and a threshold (nested Basis/
    Tier/Payload) -- confirms `_jsonable`'s recursion actually reaches
    nested dataclasses, not just the top-level rule list."""
    response = client.get("/cards/syn_ecom")
    assert response.status_code == 200
    body = response.json()

    assert len(body["caps"]) == 1
    cap = body["caps"][0]
    assert cap["measure"] == "reward"
    assert Decimal(cap["amount"]) == Decimal("1000")
    assert cap["window"]["kind"] == "calendar_month"  # nested Window dataclass reached, not dropped

    assert len(body["thresholds"]) == 1
    threshold = body["thresholds"][0]
    assert threshold["basis"]["measure"] == "waiver_eligible_spend"
    assert len(threshold["tiers"]) == 1
    assert threshold["tiers"][0]["payload"]["type"] == "waive_fee"  # nested Tier/Payload reached
