"""Integration tests for `ingest review-queue` (Part I SS I.4/I.9). Same
disposable-`zz_test_`-prefixed-fixture discipline as `tests/test_ingest_
link.py` -- read-only against the live database except for the fixture's
own setup (via `link_bundle`) and a manual `reviewer_status` flip (the
one write this file legitimately needs, mirroring what a human reviewer
would do through Supabase's Table Editor).
"""
import os

import pytest
from dotenv import load_dotenv

load_dotenv()
DATABASE_URL = os.environ.get("DATABASE_URL")

if DATABASE_URL:
    import psycopg

    try:
        psycopg.connect(DATABASE_URL, connect_timeout=5).close()
        DATABASE_REACHABLE = True
    except Exception:
        DATABASE_REACHABLE = False
else:
    DATABASE_REACHABLE = False

pytestmark = pytest.mark.skipif(not DATABASE_REACHABLE, reason="DATABASE_URL not set or not reachable")

import psycopg  # noqa: E402

from ingest.link import link_bundle  # noqa: E402
from ingest.review import build_review_queue  # noqa: E402

ISSUER_KEY = "zz_test_ingest_review_issuer"
CARD_KEY = "zz_test_ingest_review_card"
CURRENCY_KEY = "zz_test_ingest_review_currency"
SOURCE_URL = "https://example.test/zz-ingest-review-mitc.pdf"


def _cleanup(conn: psycopg.Connection) -> None:
    with conn.cursor() as cur:
        cur.execute("delete from source_links where source_id in (select id from sources where url = %s)", (SOURCE_URL,))
        cur.execute("delete from card_versions where card_id in (select id from cards where key = %s)", (CARD_KEY,))
        cur.execute("delete from cards where key = %s", (CARD_KEY,))
        cur.execute("delete from redemption_routes where currency_id in (select id from reward_currencies where key = %s)", (CURRENCY_KEY,))
        cur.execute("delete from reward_currencies where key = %s", (CURRENCY_KEY,))
        cur.execute("delete from sources where url = %s", (SOURCE_URL,))
        cur.execute("delete from issuers where key = %s", (ISSUER_KEY,))
    conn.commit()


@pytest.fixture
def conn():
    connection = psycopg.connect(DATABASE_URL, prepare_threshold=None)
    _cleanup(connection)
    yield connection
    _cleanup(connection)
    connection.close()


@pytest.fixture
def linked(conn):
    with conn.cursor() as cur:
        cur.execute(
            "insert into issuers (key, name, issuer_type) values (%s,%s,%s) returning id",
            (ISSUER_KEY, "ZZ Test Review Issuer", "bank"),
        )
    conn.commit()

    bundle = {
        "issuer_key": ISSUER_KEY, "key": CARD_KEY, "name": "ZZ Test Review Card", "network": "visa",
        "currency": CURRENCY_KEY, "effective_from": "2026-01-01",
        "sources": {"src1": {"url": SOURCE_URL, "source_type": "mitc", "title": "ZZ Test MITC"}},
        "currencies": [
            {"key": CURRENCY_KEY,
             "routes": [{"key": "stmt", "route_type": "statement_credit", "ratio": 1.0, "source_refs": ["src1"]}],
             "source_refs": ["src1"]}
        ],
        "version": {"joining_fee": 500, "annual_fee": 500, "forex_markup": 0.035, "source_refs": ["src1"]},
        "earning_rules": [
            {"key": "base", "selector": {}, "accrual": {"type": "percentage", "rate": 0.01, "rounding": "floor_paise_per_txn"},
             "priority": 10, "source_refs": ["src1"]},
        ],
    }
    return link_bundle(bundle, conn)


def test_review_queue_finds_the_new_card_and_its_currency_grouped_separately(conn, linked):
    groups = build_review_queue(conn)
    by_label = {g.label: g for g in groups}

    assert f"card:{CARD_KEY}" in by_label
    card_group = by_label[f"card:{CARD_KEY}"]
    assert card_group.card_version_id == linked.card_version_id
    entity_types = {item.entity_type for item in card_group.items}
    assert entity_types == {"card_version", "earning_rule"}

    assert f"issuer:{ISSUER_KEY} (shared currency)" in by_label
    currency_group = by_label[f"issuer:{ISSUER_KEY} (shared currency)"]
    assert currency_group.card_version_id is None  # issuer-level, not tied to one card_version
    assert {item.entity_type for item in currency_group.items} == {"reward_currency", "redemption_route"}


def test_review_queue_omits_entities_once_approved(conn, linked):
    groups_before = build_review_queue(conn)
    card_group_before = next(g for g in groups_before if g.label == f"card:{CARD_KEY}")
    assert len(card_group_before.items) == 2  # card_version + base earning_rule

    with conn.cursor() as cur:
        cur.execute(
            "update source_links set reviewer_status = 'approved' where entity_type = 'card_version' and entity_id = %s",
            (linked.card_version_id,),
        )
    conn.commit()

    groups_after = build_review_queue(conn)
    labels_after = {g.label for g in groups_after}
    if f"card:{CARD_KEY}" in labels_after:
        card_group_after = next(g for g in groups_after if g.label == f"card:{CARD_KEY}")
        assert len(card_group_after.items) == 1  # only the still-unreviewed earning_rule remains
        assert card_group_after.items[0].entity_type == "earning_rule"
    else:
        # would only happen if the earning_rule were ALSO approved -- assert that's not the case
        pytest.fail("card group disappeared entirely -- earning_rule should still be unreviewed")


def test_review_queue_empty_state_reports_no_groups_for_this_fixture(conn, linked):
    with conn.cursor() as cur:
        cur.execute(
            "update source_links set reviewer_status = 'approved'"
            " where entity_id = %s or entity_id in (select id from earning_rules where card_version_id = %s)",
            (linked.card_version_id, linked.card_version_id),
        )
    conn.commit()

    groups = build_review_queue(conn)
    labels = {g.label for g in groups}
    assert f"card:{CARD_KEY}" not in labels  # fully reviewed -- no longer in the queue
