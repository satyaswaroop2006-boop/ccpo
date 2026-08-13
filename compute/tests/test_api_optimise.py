"""FastAPI TestClient tests for /optimise (Phase 4's final module).
Reuses already-hand-verified numbers from tests/test_frontier.py and
tests/test_scenarios.py (syn_ecom + syn_flat, Rs12,00,000/yr ecommerce)
rather than re-deriving anything -- this file checks HTTP/JSON wiring and
orchestration, not a third copy of the underlying financial arithmetic.
`candidate_universe` is always pinned in these tests (never the full live
catalog) so they stay fast and don't depend on which of the 12 synthetic
cards happen to be allocate()-compatible on a given day.
"""
from decimal import Decimal

from fastapi.testclient import TestClient

from app.main import app, get_repository
from app.repository import SyntheticCatalogRepository

app.dependency_overrides[get_repository] = SyntheticCatalogRepository
client = TestClient(app)

ECOM_SPEND = [{"category": "ecommerce", "channel": "online", "annual_amount": "1200000"}]


def test_optimise_end_to_end_matches_hand_computation():
    # Same fixture as test_frontier.py's own T1-pass scenario: syn_ecom
    # alone Rs21,600.00, syn_flat alone Rs18,000.00, both Rs26,400.00.
    # DeltaV=4,800.00 clears T1; DeltaFee=0 (syn_flat has none) trivially
    # clears T2 -> recommend both cards. Robustness numbers match
    # test_scenarios.py's own Rs12,00,000/yr fixture exactly (Low sweep
    # both=Rs22,800.00).
    payload = {
        "spend": ECOM_SPEND,
        "candidate_universe": ["syn_ecom", "syn_flat"],
        "cardinality_mode": "up_to",
    }
    response = client.post("/optimise", json=payload)
    assert response.status_code == 200
    body = response.json()

    assert body["excluded_cards"] == []
    assert set(body["candidates"]) == {"syn_ecom", "syn_flat"}

    by_size = {p["size"]: p for p in body["frontier"]}
    assert Decimal(by_size[1]["pv_exact"]) == Decimal("21600.00")
    assert Decimal(by_size[2]["pv_exact"]) == Decimal("26400.00")

    assert body["recommended_size"] == 2
    assert body["capped_by_tolerance"] is False
    assert body["recommended_subset_key"] == "syn_ecom+syn_flat"
    assert Decimal(body["recommended_pv_exact"]) == Decimal("26400.00")

    step = body["recommendation_steps"][0]
    assert Decimal(step["delta_v"]) == Decimal("4800.00")
    assert step["t1_pass"] is True
    assert step["t2_pass"] is True
    assert step["passes"] is True
    assert "2nd card" in step["explanation"]

    owned = {c["card_key"]: c for c in body["classification_owned"]}
    # ICV(syn_ecom|P) = 26,400.00 - 18,000.00 = 8,400.00; ICV(syn_flat|P) = 26,400.00 - 21,600.00 = 4,800.00
    assert Decimal(owned["syn_ecom"]["icv"]) == Decimal("8400.00")
    assert owned["syn_ecom"]["label"] == "KEEP"
    assert Decimal(owned["syn_flat"]["icv"]) == Decimal("4800.00")
    assert owned["syn_flat"]["label"] == "KEEP"
    assert body["classification_candidates"] == []  # nothing outside the recommended portfolio in a 2-card universe

    assert body["robustness"] is not None
    assert Decimal(body["robustness"]["v_expected"]) == Decimal("26400.00")
    assert Decimal(body["robustness"]["v_low"]) == Decimal("22800.00")
    assert body["robustness"]["rank_stable"] is True


def test_optimise_n_tol_caps_the_recommendation():
    payload = {
        "spend": ECOM_SPEND,
        "candidate_universe": ["syn_ecom", "syn_flat"],
        "n_tol": 1,
    }
    response = client.post("/optimise", json=payload)
    assert response.status_code == 200
    body = response.json()
    assert body["recommended_size"] == 1
    assert body["capped_by_tolerance"] is True
    assert body["recommended_subset_key"] == "syn_ecom"


def test_optimise_run_scenarios_false_skips_robustness_and_t3():
    payload = {
        "spend": ECOM_SPEND,
        "candidate_universe": ["syn_ecom", "syn_flat"],
        "run_scenarios": False,
    }
    response = client.post("/optimise", json=payload)
    assert response.status_code == 200
    body = response.json()
    assert body["robustness"] is None
    assert body["recommendation_steps"][0]["t3_pass"] is None
    assert body["recommended_size"] == 2  # T1/T2 alone already recommend both


def test_optimise_excludes_an_incompatible_card_and_still_succeeds():
    # syn_slab (incremental tier_mode) fails the allocate()+repair() probe
    # unconditionally (docs/DECISIONS.md #68/#70) -- excluded with a clear
    # reason, syn_ecom alone still optimises successfully.
    payload = {
        "spend": [{"category": "grocery", "annual_amount": "120000"}],
        "candidate_universe": ["syn_ecom", "syn_slab"],
    }
    response = client.post("/optimise", json=payload)
    assert response.status_code == 200
    body = response.json()

    excluded_keys = {c["card_key"] for c in body["excluded_cards"]}
    assert excluded_keys == {"syn_slab"}
    assert "incremental-tier" in next(c["reason"] for c in body["excluded_cards"] if c["card_key"] == "syn_slab")
    assert body["candidates"] == ["syn_ecom"]
    assert body["recommended_card_keys"] == ["syn_ecom"]


def test_optimise_all_candidates_incompatible_returns_422():
    payload = {
        "spend": [{"category": "grocery", "annual_amount": "120000"}],
        "candidate_universe": ["syn_slab", "syn_points"],
    }
    response = client.post("/optimise", json=payload)
    assert response.status_code == 422


def test_optimise_unknown_candidate_universe_card_returns_404():
    payload = {
        "spend": [{"category": "grocery", "annual_amount": "120000"}],
        "candidate_universe": ["not_a_real_card"],
    }
    response = client.post("/optimise", json=payload)
    assert response.status_code == 404
