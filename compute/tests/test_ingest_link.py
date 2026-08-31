"""Integration tests for `ingest link` (Part I SS I.4/I.9, docs/DECISIONS.md
#133-#135). The FIRST `compute/` code that writes to the catalog tables via
anything other than `seeds/seed.py`'s synthetic fixtures -- skipped entirely
when `DATABASE_URL` isn't set or reachable, same posture as `tests/
test_postgres_repository.py`.

Every row this file creates uses a `zz_test_ingest_link_` prefixed key,
distinct from both the real 12-card synthetic catalog and any real card --
even if a test fails mid-way and cleanup doesn't run, the leftover rows are
unambiguous test debris, not confusable with real data. `_cleanup` runs
in a fixture's post-yield section, which pytest always executes, pass or
fail; it deletes in FK-safe order and is itself idempotent (safe to call
on a database that already has nothing to clean, which is exactly what
happens if a PRIOR failed run couldn't finish its own cleanup).
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

from ingest.link import LinkError, link_bundle  # noqa: E402

ISSUER_KEY = "zz_test_ingest_link_issuer"
CARD_KEY = "zz_test_ingest_link_card"
CARD_KEY_2 = "zz_test_ingest_link_card_2"
CURRENCY_KEY = "zz_test_ingest_link_currency"
SOURCE_URL = "https://example.test/zz-ingest-link-mitc.pdf"
SOURCE_URL_FAQ = "https://example.test/zz-ingest-link-faq.html"


def _cleanup(conn: psycopg.Connection) -> None:
    with conn.cursor() as cur:
        cur.execute(
            "delete from source_links where source_id in (select id from sources where url in (%s,%s))",
            (SOURCE_URL, SOURCE_URL_FAQ),
        )
        cur.execute(
            "delete from card_versions where card_id in (select id from cards where key in (%s,%s))",
            (CARD_KEY, CARD_KEY_2),
        )
        cur.execute("delete from cards where key in (%s,%s)", (CARD_KEY, CARD_KEY_2))
        cur.execute(
            "delete from redemption_routes where currency_id in (select id from reward_currencies where key = %s)",
            (CURRENCY_KEY,),
        )
        cur.execute("delete from reward_currencies where key = %s", (CURRENCY_KEY,))
        cur.execute("delete from sources where url in (%s,%s)", (SOURCE_URL, SOURCE_URL_FAQ))
        cur.execute("delete from issuers where key = %s", (ISSUER_KEY,))
    conn.commit()


@pytest.fixture
def conn():
    # prepare_threshold=None: see app/repository.py's PostgresCardRepository
    # / docs/DECISIONS.md #136 -- Supabase's pooler doesn't support
    # psycopg3's auto-prepared statements, and this file's repeated
    # identical link_bundle() inserts across several test cards trip it.
    connection = psycopg.connect(DATABASE_URL, prepare_threshold=None)
    _cleanup(connection)  # in case a prior interrupted run left debris
    yield connection
    _cleanup(connection)
    connection.close()


@pytest.fixture
def issuer_id(conn):
    with conn.cursor() as cur:
        cur.execute(
            "insert into issuers (key, name, issuer_type) values (%s,%s,%s) returning id",
            (ISSUER_KEY, "ZZ Test Issuer", "bank"),
        )
        iid = cur.fetchone()[0]
    conn.commit()
    return iid


def _bundle(card_key=CARD_KEY, currency_key=CURRENCY_KEY, source_url=SOURCE_URL, source_type="mitc", **overrides):
    bundle = {
        "issuer_key": ISSUER_KEY,
        "key": card_key,
        "name": "ZZ Test Ingest Link Card",
        "network": "visa",
        "currency": currency_key,
        "effective_from": "2026-01-01",
        "sources": {
            "src1": {
                "url": source_url, "source_type": source_type, "title": "ZZ Test Source",
                "storage_path": "sources/zz_test/src1.pdf", "captured_at": "2026-01-01",
            },
        },
        "currencies": [
            {
                "key": currency_key,
                "routes": [{"key": "stmt", "route_type": "statement_credit", "ratio": 1.0, "source_refs": ["src1"]}],
                "source_refs": ["src1"],
            }
        ],
        "version": {"joining_fee": 500, "annual_fee": 500, "forex_markup": 0.035, "source_refs": ["src1"]},
        "earning_rules": [
            {
                "key": "base", "selector": {},
                "accrual": {"type": "percentage", "rate": 0.01, "rounding": "floor_paise_per_txn"},
                "priority": 10, "source_refs": ["src1"],
            }
        ],
    }
    bundle.update(overrides)
    return bundle


def test_link_inserts_card_currency_rules_and_source_links(conn, issuer_id):
    result = link_bundle(_bundle(), conn)

    assert result.card_key == CARD_KEY
    assert result.sources_inserted == 1
    assert result.sources_reused == 0
    assert result.entity_counts["earning_rules"] == 1
    # source_links: card_version + reward_currency + redemption_route + earning_rule = 4
    assert result.source_links_inserted == 4

    with conn.cursor() as cur:
        cur.execute(
            "select status, joining_fee, annual_fee from card_versions where id = %s", (result.card_version_id,)
        )
        status, joining_fee, annual_fee = cur.fetchone()
        assert status == "draft"
        assert float(joining_fee) == 500.0
        assert float(annual_fee) == 500.0

        cur.execute("select count(*) from earning_rules where card_version_id = %s", (result.card_version_id,))
        assert cur.fetchone()[0] == 1

        cur.execute(
            "select distinct reviewer_status, confidence from source_links"
            " where source_id in (select id from sources where url = %s)",
            (SOURCE_URL,),
        )
        rows = cur.fetchall()
        assert rows == [("unreviewed", "high")]  # mitc -> high, per I.1's own weighting


def test_link_refuses_when_lint_fails_and_inserts_nothing(conn, issuer_id):
    bad = _bundle()
    del bad["earning_rules"][0]["source_refs"]  # no citation -> provenance_completeness fails

    with pytest.raises(LinkError, match="ingest lint failed"):
        link_bundle(bad, conn)

    with conn.cursor() as cur:
        cur.execute("select count(*) from cards where key = %s", (CARD_KEY,))
        assert cur.fetchone()[0] == 0


def test_link_refuses_when_issuer_missing(conn):
    # no issuer_id fixture used -- issuer genuinely doesn't exist
    with pytest.raises(LinkError, match="does not exist"):
        link_bundle(_bundle(), conn)


def test_link_refuses_when_card_already_exists(conn, issuer_id):
    link_bundle(_bundle(), conn)

    with pytest.raises(LinkError, match="already exists"):
        link_bundle(_bundle(), conn)

    with conn.cursor() as cur:
        cur.execute("select count(*) from card_versions where card_id in (select id from cards where key = %s)", (CARD_KEY,))
        assert cur.fetchone()[0] == 1  # second attempt inserted nothing


def test_link_refuses_currency_owned_by_a_different_issuer(conn, issuer_id):
    # "cashback_inr" belongs to the synthetic_bank fixture issuer, not this test's issuer.
    with pytest.raises(LinkError, match="different issuer"):
        link_bundle(_bundle(currency_key="cashback_inr"), conn)

    with conn.cursor() as cur:
        cur.execute("select count(*) from cards where key = %s", (CARD_KEY,))
        assert cur.fetchone()[0] == 0


def test_link_reuses_currency_and_routes_across_two_cards_of_the_same_issuer(conn, issuer_id):
    result1 = link_bundle(_bundle(card_key=CARD_KEY), conn)
    result2 = link_bundle(_bundle(card_key=CARD_KEY_2), conn)

    with conn.cursor() as cur:
        cur.execute("select id from reward_currencies where key = %s", (CURRENCY_KEY,))
        rows = cur.fetchall()
        assert len(rows) == 1  # not duplicated

        cur.execute(
            "select count(*) from redemption_routes where currency_id = %s", (rows[0][0],)
        )
        assert cur.fetchone()[0] == 1  # not duplicated either

    # second card's currency/route were REUSED, not newly cited again --
    # only its own card_version + earning_rule get source_links (2), not
    # the reward_currency/redemption_route ones result1 already covered.
    assert result2.source_links_inserted == 2
    assert result1.source_links_inserted == 4


def test_link_dedupes_sources_by_url_but_links_each_citing_entity_separately(conn, issuer_id):
    link_bundle(_bundle(card_key=CARD_KEY), conn)
    result2 = link_bundle(_bundle(card_key=CARD_KEY_2), conn)

    with conn.cursor() as cur:
        cur.execute("select id from sources where url = %s", (SOURCE_URL,))
        rows = cur.fetchall()
        assert len(rows) == 1  # deduped by URL, not re-inserted

    assert result2.sources_inserted == 0
    assert result2.sources_reused == 1


def test_confidence_derived_mechanically_from_source_type(conn, issuer_id):
    result = link_bundle(_bundle(source_url=SOURCE_URL_FAQ, source_type="faq"), conn)
    assert result.card_key == CARD_KEY

    with conn.cursor() as cur:
        cur.execute(
            "select distinct confidence from source_links where source_id in (select id from sources where url = %s)",
            (SOURCE_URL_FAQ,),
        )
        assert cur.fetchall() == [("low",)]  # faq -> low, per I.1's own weighting
