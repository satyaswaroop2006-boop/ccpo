"""Unit tests for `ingest.reward_catalog_ratio` -- no network access, a small
synthetic fixture matching the real `window.rewards = {...}` shape confirmed
by hand against the live page on 2026-09-03 (see docs/DECISIONS.md's PRIME
entry). Exercises extraction + parsing + per-segment statistics only; the
live-fetch path (`fetch_catalog_html`) is exercised for real by whoever runs
`ingest reward-catalog-ratio` when actually refreshing a reference snapshot,
not by this fast/offline suite.
"""
import json

import pytest

from ingest.reward_catalog_ratio import (
    CatalogFetchError,
    extract_catalog_json,
    parse_catalog_items,
    refresh_reference,
    segment_stats,
)

_FIXTURE_REWARDS = {
    "reward": [
        {
            "card": "sbi-card-prime,sbi-card-elite",
            "itemName": "Flipkart e-Gift Voucher INR 1000",
            "brand": "flipkart",
            "item": [{"itemCode": "VG1", "point": "7800", "cashPoint": "3700", "cashAmount": "499"}],
        },
        {
            "card": "sbi-card-prime",
            "itemName": "Bata E-Voucher Rs 1000",
            "brand": "bata",
            "item": [{"itemCode": "VG2", "point": "5000", "cashPoint": "0", "cashAmount": "0"}],
        },
        {
            "card": "titan-sbi-card",
            "itemName": "Titan Retail e-Voucher Rs 10000",
            "brand": "titan",
            "item": [{"itemCode": "VG3", "point": "40000", "cashPoint": "0", "cashAmount": "0"}],
        },
        {
            # No explicit rupee value in the name (a physical product) -- must be excluded from ratio stats.
            "card": "sbi-card-prime",
            "itemName": "Okhaya EV Helmet",
            "brand": "okhaya",
            "item": [{"itemCode": "VG4", "point": "9900", "cashPoint": "4950", "cashAmount": "700"}],
        },
    ]
}


def _html_with_blob(blob: dict) -> str:
    return f"<html><script>window.rewards = {json.dumps(blob)};\nsomeOtherVar = 1;</script></html>"


def test_extract_catalog_json_parses_the_embedded_blob():
    data = extract_catalog_json(_html_with_blob(_FIXTURE_REWARDS))
    assert len(data["reward"]) == 4


def test_extract_catalog_json_raises_when_marker_missing():
    with pytest.raises(CatalogFetchError, match="not found"):
        extract_catalog_json("<html>no js variable here</html>")


def test_parse_catalog_items_extracts_rupee_value_from_name():
    data = extract_catalog_json(_html_with_blob(_FIXTURE_REWARDS))
    items = parse_catalog_items(data)
    assert len(items) == 4

    flipkart = next(i for i in items if i.brand == "flipkart")
    assert flipkart.rupee_value == 1000.0
    assert flipkart.points == 7800.0
    assert flipkart.card_keys == ("sbi-card-prime", "sbi-card-elite")
    assert flipkart.pure_ratio_per_100_points == pytest.approx(1000 / 7800 * 100)

    helmet = next(i for i in items if i.brand == "okhaya")
    assert helmet.rupee_value is None  # no INR/Rs figure in "Okhaya EV Helmet"
    assert helmet.pure_ratio_per_100_points is None


def test_segment_stats_scopes_to_the_named_card_and_excludes_unvalued_items():
    data = extract_catalog_json(_html_with_blob(_FIXTURE_REWARDS))
    items = parse_catalog_items(data)

    prime_stats = segment_stats(items, "sbi-card-prime")
    # Flipkart (shared) + Bata (prime-only) have explicit values; the helmet is excluded (no value).
    assert prime_stats.n == 2
    assert prime_stats.min_per_100_points == pytest.approx(1000 / 7800 * 100)  # Flipkart, ~12.82
    assert prime_stats.max_per_100_points == pytest.approx(1000 / 5000 * 100)  # Bata, 20.0

    titan_stats = segment_stats(items, "titan-sbi-card")
    assert titan_stats.n == 1
    assert titan_stats.mean_per_100_points == pytest.approx(10000 / 40000 * 100)  # 25.0

    assert segment_stats(items, "no-such-card") is None


def test_refresh_reference_reports_not_found_for_an_absent_card_key():
    fetcher = lambda: _html_with_blob(_FIXTURE_REWARDS)  # noqa: E731
    report = refresh_reference(["sbi-card-prime", "no-such-card"], fetcher=fetcher, today="2026-09-05")
    assert report["captured_at"] == "2026-09-05"
    assert report["total_catalog_items_parsed"] == 4
    assert report["segments"]["sbi-card-prime"]["n"] == 2
    assert report["segments"]["no-such-card"].startswith("not_found")
