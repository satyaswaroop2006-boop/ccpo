"""Integration tests for `ingest publish` (Part I SS I.4/I.8/I.9). Same
disposable-fixture discipline as `tests/test_ingest_link.py`, PLUS one
extra concern this file alone has: publishing is IRREVERSIBLE (Part D
Decision 2 -- a published `card_versions` row can never be UPDATEd or
DELETEd again, only moved to 'deprecated'). A naive test of the SUCCESS
path would therefore leave a permanent, un-cleanable fake row in the
shared database forever.

Fix: psycopg3 nests `conn.transaction()` blocks as SAVEPOINTs when
already inside an outer transaction, releasing (not truly committing)
on success. Wrapping the call to `publish_card_version` in the test's
OWN outer `conn.transaction()` and then deliberately raising to force
that outer transaction to roll back undoes the status flip completely --
verified empirically before writing these tests (see docs/DECISIONS.md)
-- so the success path is exercised for real (the actual UPDATE runs,
the actual gate logic runs) without ever durably publishing anything.
"""
import json
import os
from pathlib import Path

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
from ingest.publish import PublishError, publish_card_version  # noqa: E402
from seeds.synthetic_cards import CARDS  # noqa: E402

ISSUER_KEY = "zz_test_ingest_publish_issuer"
CARD_KEY = "zz_test_ingest_publish_card"
CURRENCY_KEY = "zz_test_ingest_publish_currency"
SOURCE_URL = "https://example.test/zz-ingest-publish-mitc.pdf"


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
def issuer_id(conn):
    with conn.cursor() as cur:
        cur.execute(
            "insert into issuers (key, name, issuer_type) values (%s,%s,%s) returning id",
            (ISSUER_KEY, "ZZ Test Publish Issuer", "bank"),
        )
        iid = cur.fetchone()[0]
    conn.commit()
    return iid


def _bundle():
    return {
        "issuer_key": ISSUER_KEY, "key": CARD_KEY, "name": "ZZ Test Publish Card", "network": "visa",
        "currency": CURRENCY_KEY, "effective_from": "2026-01-01",
        "sources": {"src1": {
            "url": SOURCE_URL, "source_type": "mitc", "title": "ZZ Test MITC",
            "storage_path": "sources/zz_test/src1.pdf", "captured_at": "2026-01-01",
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


# Hand-computed against _bundle() above: grocery ticket 700, 700*1%=7.00
# exact (no rounding_estimated). gross = 1,20,000*0.01 = 1,200.00. No caps/
# thresholds -> fee unwaived: steady_fee = 500*1.18=590.00, year1_fee =
# (500+500)*1.18=1,180.00. NACV steady = 1,200.00-590.00=610.00.
# NACV year1 = 1,200.00-1,180.00=20.00.
_MATCHING_GOLDEN = {
    "spend_annual": {"grocery": 120000},
    "expected": {
        "gross_reward_value": 1200.00, "fee_paid": 590.00,
        "nacv_steady_state": 610.00, "nacv_year_1": 20.00,
    },
    "tolerance_rupees": 0.01,
}

_MISMATCHED_GOLDEN = {
    "spend_annual": {"grocery": 120000},
    "expected": {"gross_reward_value": 999999.00},  # deliberately wrong
    "tolerance_rupees": 0.01,
}

_MULTI_SCENARIO_GOLDEN = {
    "scenario_wrong": {
        "spend_annual": {"grocery": 120000},
        "expected": {"gross_reward_value": 1.00},  # wrong on purpose
    },
    "scenario_right": _MATCHING_GOLDEN,
}


def _write_golden(tmp_path: Path, data: dict, name: str = "golden.json") -> str:
    path = tmp_path / name
    path.write_text(json.dumps(data))
    return str(path)


def _link(conn):
    return link_bundle(_bundle(), conn)


def _approve_everything(conn, card_version_id) -> None:
    with conn.cursor() as cur:
        cur.execute(
            "update source_links set reviewer_status = 'approved'"
            " where entity_id = %s or entity_id in (select id from earning_rules where card_version_id = %s)",
            (card_version_id, card_version_id),
        )
    conn.commit()


def test_publish_refuses_when_card_version_does_not_exist(conn):
    with pytest.raises(PublishError, match="does not exist"):
        publish_card_version(conn, "00000000-0000-0000-0000-000000000000", [])


def test_publish_refuses_on_an_already_published_card(conn):
    # Any of the real 12 synthetic cards is already status='published' --
    # read-only probe (fails before any UPDATE is attempted), safe to run
    # against the real catalog.
    with conn.cursor() as cur:
        cur.execute(
            "select cv.id from cards c join card_versions cv on cv.card_id = c.id where c.key = %s",
            (CARDS[0]["key"],),
        )
        cv_id = cur.fetchone()[0]

    with pytest.raises(PublishError, match="not 'draft'"):
        publish_card_version(conn, cv_id, [])


def test_publish_refuses_when_no_golden_given(conn, issuer_id):
    result = _link(conn)
    _approve_everything(conn, result.card_version_id)

    with pytest.raises(PublishError, match="no --golden path given"):
        publish_card_version(conn, result.card_version_id, [])


def test_publish_refuses_when_source_links_not_all_approved(conn, issuer_id, tmp_path):
    result = _link(conn)  # fresh link -- everything defaults to 'unreviewed'
    golden_path = _write_golden(tmp_path, _MATCHING_GOLDEN)

    with pytest.raises(PublishError, match="not approved"):
        publish_card_version(conn, result.card_version_id, [golden_path])

    with conn.cursor() as cur:
        cur.execute("select status from card_versions where id = %s", (result.card_version_id,))
        assert cur.fetchone()[0] == "draft"  # refused before touching status


def test_publish_refuses_when_no_golden_scenario_passes(conn, issuer_id, tmp_path):
    result = _link(conn)
    _approve_everything(conn, result.card_version_id)
    golden_path = _write_golden(tmp_path, _MISMATCHED_GOLDEN)

    with pytest.raises(PublishError, match="no passing golden scenario"):
        publish_card_version(conn, result.card_version_id, [golden_path])


def test_publish_refuses_when_db_state_has_drifted_from_what_lint_validated(conn, issuer_id, tmp_path):
    """Publish re-validates engine-compatibility against what's ACTUALLY
    in the database right now, not just what the original bundle file
    said -- this proves that re-validation is real, not a rubber stamp,
    by corrupting one row directly after a clean LINK."""
    result = _link(conn)
    _approve_everything(conn, result.card_version_id)
    golden_path = _write_golden(tmp_path, _MATCHING_GOLDEN)

    with conn.cursor() as cur:
        # networks is a genuinely unsupported selector field (Phase 5 Task
        # A) -- simulates the DB drifting to something LINT would have
        # rejected had it been there originally.
        cur.execute(
            "update earning_rules set selector = '{\"networks\": [\"rupay\"]}'::jsonb"
            " where card_version_id = %s and key = 'base'",
            (result.card_version_id,),
        )
    conn.commit()

    with pytest.raises(PublishError, match="cannot be matched against"):
        publish_card_version(conn, result.card_version_id, [golden_path])


def test_publish_succeeds_and_reports_scenario_results_without_leaving_a_permanent_published_row(conn, issuer_id, tmp_path):
    result = _link(conn)
    _approve_everything(conn, result.card_version_id)
    golden_path = _write_golden(tmp_path, _MATCHING_GOLDEN)

    class _ForceRollback(Exception):
        pass

    with pytest.raises(_ForceRollback):
        with conn.transaction():  # publish_card_version's own transaction nests as a SAVEPOINT under this
            publish_result = publish_card_version(conn, result.card_version_id, [golden_path])

            assert publish_result.card_key == CARD_KEY
            assert len(publish_result.scenario_results) == 1
            assert publish_result.scenario_results[0].passed is True
            assert publish_result.scenario_results[0].diffs == ()

            with conn.cursor() as cur:
                cur.execute("select status, published_at from card_versions where id = %s", (result.card_version_id,))
                status, published_at = cur.fetchone()
                assert status == "published"
                assert published_at is not None

            raise _ForceRollback()  # undo everything above -- never actually commits

    # Proof the rollback genuinely worked: status is back to 'draft', so
    # normal (non-published-row) cleanup in the `conn` fixture can proceed.
    with conn.cursor() as cur:
        cur.execute("select status from card_versions where id = %s", (result.card_version_id,))
        assert cur.fetchone()[0] == "draft"


def test_publish_accepts_a_multi_scenario_golden_file_needing_only_one_pass(conn, issuer_id, tmp_path):
    """Mirrors compute/ingestion/golden_sbi_cashback.json's own shape:
    several named scenarios in one file, only one of which needs to pass
    (Part I SS I.8: "at least one")."""
    result = _link(conn)
    _approve_everything(conn, result.card_version_id)
    golden_path = _write_golden(tmp_path, _MULTI_SCENARIO_GOLDEN)

    class _ForceRollback(Exception):
        pass

    with pytest.raises(_ForceRollback):
        with conn.transaction():
            publish_result = publish_card_version(conn, result.card_version_id, [golden_path])
            assert len(publish_result.scenario_results) == 2
            by_name = {r.scenario_name: r.passed for r in publish_result.scenario_results}
            assert by_name == {"scenario_wrong": False, "scenario_right": True}
            raise _ForceRollback()
