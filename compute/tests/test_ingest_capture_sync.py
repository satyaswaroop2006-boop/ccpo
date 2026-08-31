"""Integration tests for `sync_captured_sources_to_db` (Part I SS I.1,
docs/DECISIONS.md #144) -- the one part of `ingest capture` that touches
Postgres, for a source that was `ingest link`ed before capture ever ran
(CASHBACK SBI's own real situation). Same disposable-`zz_test_`-prefixed-
fixture discipline as `tests/test_ingest_link.py`; confirmed safe against
an ALREADY-PUBLISHED card_version specifically, since one of these tests
runs it against a real published synthetic card and checks nothing about
its rule data changes -- only `sources.storage_path` (a table with no
immutability trigger at all, verified directly against `0001_init.sql`,
docs/DECISIONS.md #144).
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

from ingest.capture import sync_captured_sources_to_db  # noqa: E402
from ingest.link import link_bundle  # noqa: E402

ISSUER_KEY = "zz_test_ingest_capture_sync_issuer"
CARD_KEY = "zz_test_ingest_capture_sync_card"
CURRENCY_KEY = "zz_test_ingest_capture_sync_currency"
SOURCE_URL = "https://example.test/zz-ingest-capture-sync-mitc.pdf"


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
            "insert into issuers (key, name, issuer_type) values (%s,%s,%s)",
            (ISSUER_KEY, "ZZ Test Capture Sync Issuer", "bank"),
        )
    conn.commit()

    bundle = {
        "issuer_key": ISSUER_KEY, "key": CARD_KEY, "name": "ZZ Test Capture Sync Card", "network": "visa",
        "currency": CURRENCY_KEY, "effective_from": "2026-01-01",
        "sources": {"src1": {
            "url": SOURCE_URL, "source_type": "mitc", "title": "ZZ Test MITC",
            # link_bundle now requires storage_path/captured_at (Phase 5 #143) --
            # a deliberately stale placeholder here, since these tests are about
            # sync_captured_sources_to_db's UPDATE, not link_bundle's own INSERT.
            "storage_path": "sources/zz_test_issuer/src1-STALE-PLACEHOLDER.pdf", "captured_at": "2026-01-01",
        }},
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
    link_bundle(bundle, conn)
    return bundle


def test_sync_updates_the_matching_source_row_by_url(conn, linked):
    # simulates a real (re-)capture superseding the stale placeholder the
    # fixture linked with
    linked["sources"]["src1"]["storage_path"] = "sources/zz_test_issuer/src1-2026-08-31.pdf"
    linked["sources"]["src1"]["captured_at"] = "2026-08-31"

    updated = sync_captured_sources_to_db(conn, linked)
    assert updated == ["src1"]

    with conn.cursor() as cur:
        cur.execute("select storage_path, captured_at from sources where url = %s", (SOURCE_URL,))
        storage_path, captured_at = cur.fetchone()
        assert storage_path == "sources/zz_test_issuer/src1-2026-08-31.pdf"
        assert captured_at.date().isoformat() == "2026-08-31"  # captured_at is timestamptz, not date


def test_sync_skips_sources_not_captured_in_this_run(conn, linked):
    # the local bundle dict has no storage_path/captured_at for this run
    # (e.g. re-loaded fresh from disk before a source was actually
    # re-captured) -- sync only pushes what IS present locally, it never
    # invents or clears a value.
    del linked["sources"]["src1"]["storage_path"]
    del linked["sources"]["src1"]["captured_at"]
    updated = sync_captured_sources_to_db(conn, linked)
    assert updated == []


def test_sync_skips_a_source_with_no_matching_db_row(conn):
    """The normal, forward-looking case: capture runs on a bundle BEFORE
    ingest link ever does. No matching sources row exists yet -- silently
    skipped, not an error."""
    bundle = {
        "sources": {
            "unlinked": {
                "url": "https://example.test/never-linked.pdf",
                "storage_path": "sources/x/unlinked-2026-08-31.pdf", "captured_at": "2026-08-31",
            },
        },
    }
    updated = sync_captured_sources_to_db(conn, bundle)
    assert updated == []


def test_sync_does_not_touch_card_versions_or_any_rule_table(conn, linked):
    """`sync_captured_sources_to_db`'s own UPDATE statement only ever
    names the `sources` table -- confirmed here by asserting the linked
    card's card_version/earning_rule rows are byte-identical before and
    after a sync. Whether that UPDATE would be ALLOWED against an
    already-published card_version is a separate, structural question
    already answered directly against `0001_init.sql`'s trigger
    definitions (docs/DECISIONS.md #144): the immutability triggers are
    attached to exactly `caps/earning_rules/thresholds/exclusions/
    benefits/surcharges` and `card_versions` itself -- `sources` is not
    among them and carries no trigger at all, so Postgres would not
    reject this UPDATE regardless of publish status. Not re-verified
    live here against an actually-published throwaway row: doing so
    would either need to flip a disposable test card_version to
    'published' (permanently undeletable afterward -- Part D Decision 2
    has no test-data exemption, which would leave permanent debris in
    the shared catalog) or fight `sync_captured_sources_to_db`'s own
    internal `conn.commit()` against a savepoint-rollback pattern
    (psycopg3 forbids calling `commit()`/`rollback()` from inside an
    active `Transaction` block) -- both worse trade-offs than trusting
    the already-verified, unchanging trigger definitions directly."""
    with conn.cursor() as cur:
        cur.execute(
            "select cv.status, cv.joining_fee, cv.annual_fee from card_versions cv"
            " join cards c on c.id = cv.card_id where c.key = %s", (CARD_KEY,),
        )
        cv_before = cur.fetchone()
        cur.execute("select key, selector, accrual from earning_rules where card_version_id ="
                    " (select id from card_versions where card_id = (select id from cards where key=%s))", (CARD_KEY,))
        rules_before = cur.fetchall()

    linked["sources"]["src1"]["storage_path"] = "sources/zz_test_issuer/src1-2026-08-31.pdf"
    linked["sources"]["src1"]["captured_at"] = "2026-08-31"
    sync_captured_sources_to_db(conn, linked)

    with conn.cursor() as cur:
        cur.execute(
            "select cv.status, cv.joining_fee, cv.annual_fee from card_versions cv"
            " join cards c on c.id = cv.card_id where c.key = %s", (CARD_KEY,),
        )
        assert cur.fetchone() == cv_before
        cur.execute("select key, selector, accrual from earning_rules where card_version_id ="
                    " (select id from card_versions where card_id = (select id from cards where key=%s))", (CARD_KEY,))
        assert cur.fetchall() == rules_before
