"""FastAPI TestClient tests for /evaluate and /next-best-spend (Phase 3),
backed by the SyntheticCatalogRepository -- no DATABASE_URL needed. These
check the HTTP/JSON shape and delegate correctness to the same numbers
`tests/test_evaluate_orchestrator.py` already proved against the golden
battery; `test_evaluate_matches_golden_syn_ecom_basic` cross-checks one
end-to-end HTTP round trip against `golden_syn_ecom_basic.json` directly.
"""
import json
from decimal import Decimal
from pathlib import Path

from fastapi.testclient import TestClient

from app.main import app

GOLDENS_DIR = Path(__file__).resolve().parent.parent / "goldens"

client = TestClient(app)


def test_health():
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_evaluate_unknown_card_returns_404():
    response = client.post("/evaluate", json={"card_key": "not_a_real_card", "spend": []})
    assert response.status_code == 404


def _spend_items_from_golden(spend_annual: dict) -> list[dict]:
    # golden JSON keys use test_goldens.py's "category[/channel]" shorthand
    # (see docs/DECISIONS.md #5); the API takes category/channel as separate
    # fields, so split it here rather than teaching the schema that spelling.
    items = []
    for key, amount in spend_annual.items():
        category, _, channel = key.partition("/")
        item = {"category": category, "annual_amount": str(amount)}
        if channel:
            item["channel"] = channel
        items.append(item)
    return items


def test_evaluate_matches_golden_syn_ecom_basic():
    golden = json.loads((GOLDENS_DIR / "golden_syn_ecom_basic.json").read_text())
    payload = {
        "card_key": "syn_ecom",
        "spend": _spend_items_from_golden(golden["spend_annual"]),
    }
    response = client.post("/evaluate", json=payload)
    assert response.status_code == 200
    body = response.json()

    expected = golden["expected"]
    assert Decimal(body["gross_reward_value"]) == Decimal(str(expected["gross_reward_value"]))
    assert Decimal(body["fee_steady"]) == Decimal(str(expected["fee_paid"]))
    assert body["waiver_achieved"] == expected["waiver_achieved"]
    assert Decimal(body["nacv"]["steady_state"]) == Decimal(str(expected["nacv_steady_state"]))
    assert Decimal(body["nacv"]["year_1"]) == Decimal(str(expected["nacv_year_1"]))
    assert body["nacv"]["trace"]  # non-empty -- Stage 11's line items came through


def test_evaluate_matches_golden_syn_points_portal_stacking():
    # exercises merchant_group ("~" spend-item field), a multi-route
    # currency requiring a primary_route declaration, and a rule_group cap.
    golden = json.loads((GOLDENS_DIR / "golden_syn_points_portal_stacking.json").read_text())
    payload = {
        "card_key": "syn_points",
        "spend": [
            {"category": "hotels_domestic", "annual_amount": "1800000", "merchant_group": "synth_portal"},
            {"category": "dining", "annual_amount": "360000"},
        ],
        "assumptions": {"primary_routes": golden["assumptions"]["primary_route"]},
    }
    response = client.post("/evaluate", json=payload)
    assert response.status_code == 200
    body = response.json()

    expected = golden["expected"]
    assert Decimal(body["gross_reward_value"]) == Decimal(str(expected["gross_reward_value"]))
    assert Decimal(body["nacv"]["steady_state"]) == Decimal(str(expected["nacv_steady_state"]))
    assert Decimal(body["nacv"]["three_year"]) == Decimal(str(expected["nacv_3yr"]))


def test_next_best_spend_ranks_ecom_category_above_flat_dining():
    # syn_ecom: base 1% everywhere, but a capped 5% accelerator on
    # ecommerce/online spend -- routing the next rupees to ecommerce should
    # score higher than routing them to an unaccelerated category (dining,
    # which only ever earns syn_ecom's 1% base) on the same card.
    payload = {
        "baseline_spend": [{"category": "dining", "annual_amount": "60000"}],
        "candidates": [
            {"card_key": "syn_ecom", "category": "ecommerce", "channel": "online"},
            {"card_key": "syn_ecom", "category": "dining"},
        ],
        "deltas": ["10000"],
    }
    response = client.post("/next-best-spend", json=payload)
    assert response.status_code == 200
    results = response.json()["results"]
    assert len(results) == 2

    by_category = {r["category"]: r for r in results}
    ecommerce_rate = Decimal(by_category["ecommerce"]["delta_nacv_rate"])
    dining_rate = Decimal(by_category["dining"]["delta_nacv_rate"])
    assert ecommerce_rate > dining_rate
    # base-rate-only category: exactly 1% net (no cap, no fee change from this delta)
    assert dining_rate == Decimal("0.01")
    # ranked best-first
    assert results[0]["category"] == "ecommerce"


def test_next_best_spend_unknown_card_returns_404():
    payload = {
        "baseline_spend": [],
        "candidates": [{"card_key": "not_a_real_card", "category": "dining"}],
    }
    response = client.post("/next-best-spend", json=payload)
    assert response.status_code == 404
