"""Integration tests for Part I SS I.6's devaluation flow: `ingest link
--new-version` (creates a new draft card_versions row superseding the
card's latest PUBLISHED one) and `ingest publish`'s own predecessor-
closing behaviour (sets the OLD version's `effective_to` in the same
transaction it flips the new one to `published`).

Same disposable-`zz_test_`-prefixed-fixture discipline as `tests/
test_ingest_link.py`/`test_ingest_publish.py`. The full end-to-end cycle
test (link v1 -> approve -> publish v1 -> link v2 --new-version ->
approve -> publish v2 -> confirm v1.effective_to) needs BOTH publishes
to actually happen for real (v2's `--new-version` guard requires v1 to
genuinely be `status='published'`, not just "as if") -- but a published
`card_versions` row can never be deleted (Part D Decision 2, no test-
data exemption). Solved the same way `test_ingest_publish.py` solves it:
the entire cycle runs inside ONE outer `conn.transaction()` that's
deliberately rolled back at the end, so every UPDATE/INSERT along the
way is real (both publishes genuinely flip `status='published'` and are
visible to each other within the same transaction -- Postgres reads a
transaction's own uncommitted writes) but nothing durably persists.
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

from ingest.link import LinkError, link_bundle  # noqa: E402
from ingest.publish import PublishError, publish_card_version  # noqa: E402

ISSUER_KEY = "zz_test_ingest_devaluation_issuer"
CARD_KEY = "zz_test_ingest_devaluation_card"
CURRENCY_KEY = "zz_test_ingest_devaluation_currency"
SOURCE_URL_V1 = "https://example.test/zz-ingest-devaluation-v1.pdf"
SOURCE_URL_V2 = "https://example.test/zz-ingest-devaluation-v2-announcement.pdf"


def _cleanup(conn: psycopg.Connection) -> None:
    with conn.cursor() as cur:
        cur.execute(
            "delete from source_links where source_id in (select id from sources where url in (%s,%s))",
            (SOURCE_URL_V1, SOURCE_URL_V2),
        )
        cur.execute("delete from card_versions where card_id in (select id from cards where key = %s)", (CARD_KEY,))
        cur.execute("delete from cards where key = %s", (CARD_KEY,))
        cur.execute("delete from redemption_routes where currency_id in (select id from reward_currencies where key = %s)", (CURRENCY_KEY,))
        cur.execute("delete from reward_currencies where key = %s", (CURRENCY_KEY,))
        cur.execute("delete from sources where url in (%s,%s)", (SOURCE_URL_V1, SOURCE_URL_V2))
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
            (ISSUER_KEY, "ZZ Test Devaluation Issuer", "bank"),
        )
        iid = cur.fetchone()[0]
    conn.commit()
    return iid


def _bundle(rate=0.01, effective_from="2026-01-01", source_url=SOURCE_URL_V1, name="ZZ Test Devaluation Card"):
    return {
        "issuer_key": ISSUER_KEY, "key": CARD_KEY, "name": name, "network": "visa",
        "currency": CURRENCY_KEY, "effective_from": effective_from,
        "sources": {"src1": {
            "url": source_url, "source_type": "mitc", "title": "ZZ Test Source",
            "storage_path": "sources/zz_test/src1.pdf", "captured_at": effective_from,
        }},
        "currencies": [
            {"key": CURRENCY_KEY,
             "routes": [{"key": "stmt", "route_type": "statement_credit", "ratio": 1.0, "source_refs": ["src1"]}],
             "source_refs": ["src1"]}
        ],
        "version": {"joining_fee": 500, "annual_fee": 500, "forex_markup": 0.035, "source_refs": ["src1"]},
        "earning_rules": [
            {"key": "base", "selector": {}, "accrual": {"type": "percentage", "rate": rate, "rounding": "floor_paise_per_txn"},
             "priority": 10, "source_refs": ["src1"]},
        ],
    }


def _approve_everything(conn, card_version_id) -> None:
    # Covers the card_version's own rule-level entities AND its currency/
    # route (docs/DECISIONS.md #148 -- ingest publish's gate now checks both).
    with conn.cursor() as cur:
        cur.execute(
            "update source_links set reviewer_status = 'approved'"
            " where entity_id = %s"
            " or entity_id in (select id from earning_rules where card_version_id = %s)"
            " or entity_id in (select currency_id from card_versions where id = %s)"
            " or entity_id in (select id from redemption_routes where currency_id ="
            "   (select currency_id from card_versions where id = %s))",
            (card_version_id, card_version_id, card_version_id, card_version_id),
        )


def _write_golden(tmp_path: Path, rate: float, name: str) -> str:
    # grocery ticket 700, 700*rate lands exact for 0.01/0.02 -> no rounding_estimated.
    # gross = 1,20,000*rate. fee unwaived: steady_fee=590.00.
    gross = round(120000 * rate, 2)
    data = {
        "spend_annual": {"grocery": 120000},
        "expected": {"gross_reward_value": gross, "fee_paid": 590.00, "nacv_steady_state": round(gross - 590.00, 2)},
        "tolerance_rupees": 0.01,
    }
    path = tmp_path / name
    path.write_text(json.dumps(data))
    return str(path)


# ---------------------------------------------------------------------------
# ingest link --new-version: refusals
# ---------------------------------------------------------------------------

def test_new_version_refuses_when_card_does_not_exist(conn, issuer_id):
    with pytest.raises(LinkError, match="does not exist yet"):
        link_bundle(_bundle(), conn, new_version=True)


def test_new_version_refuses_when_latest_version_is_still_draft(conn, issuer_id):
    link_bundle(_bundle(), conn)  # v1, still draft -- never published
    with pytest.raises(LinkError, match="not 'published'"):
        link_bundle(_bundle(rate=0.02, effective_from="2026-06-01"), conn, new_version=True)


def test_default_link_still_refuses_when_card_already_exists(conn, issuer_id):
    link_bundle(_bundle(), conn)
    with pytest.raises(LinkError, match="already exists"):
        link_bundle(_bundle(), conn, new_version=False)


# ---------------------------------------------------------------------------
# The rest need a genuinely PUBLISHED v1 to build on -- everything below
# runs inside one outer transaction, rolled back at the very end (see
# module docstring for why).
# ---------------------------------------------------------------------------

class _ForceRollback(Exception):
    pass


def _link_approve_publish_v1(conn, tmp_path):
    result1 = link_bundle(_bundle(rate=0.01, effective_from="2026-01-01"), conn)
    _approve_everything(conn, result1.card_version_id)
    golden1 = _write_golden(tmp_path, 0.01, "v1.json")
    publish1 = publish_card_version(conn, result1.card_version_id, [golden1])
    return result1, publish1


def test_new_version_refuses_on_name_mismatch(conn, issuer_id, tmp_path):
    with pytest.raises(_ForceRollback):
        with conn.transaction():
            _link_approve_publish_v1(conn, tmp_path)
            with pytest.raises(LinkError, match="doesn't match the existing card's name"):
                link_bundle(_bundle(rate=0.02, effective_from="2026-06-01", name="A Totally Different Name"), conn, new_version=True)
            raise _ForceRollback()


def test_new_version_refuses_on_non_later_effective_from(conn, issuer_id, tmp_path):
    with pytest.raises(_ForceRollback):
        with conn.transaction():
            _link_approve_publish_v1(conn, tmp_path)
            with pytest.raises(LinkError, match="must be AFTER"):
                link_bundle(_bundle(rate=0.02, effective_from="2026-01-01"), conn, new_version=True)  # same date as v1
            raise _ForceRollback()


def test_new_version_creates_version_2_reusing_card_and_currency(conn, issuer_id, tmp_path):
    with pytest.raises(_ForceRollback):
        with conn.transaction():
            result1, _ = _link_approve_publish_v1(conn, tmp_path)
            result2 = link_bundle(_bundle(rate=0.02, effective_from="2026-06-01", source_url=SOURCE_URL_V2), conn, new_version=True)

            assert result2.version_no == 2
            assert result2.card_id == result1.card_id  # same card, reused, not re-inserted

            with conn.cursor() as cur:
                cur.execute("select count(*) from cards where key = %s", (CARD_KEY,))
                assert cur.fetchone()[0] == 1  # still exactly one cards row
                cur.execute("select id from reward_currencies where key = %s", (CURRENCY_KEY,))
                assert len(cur.fetchall()) == 1  # currency reused, not duplicated
                cur.execute("select count(*) from card_versions where card_id = %s", (result1.card_id,))
                assert cur.fetchone()[0] == 2  # v1 and v2 both exist

            raise _ForceRollback()


def test_full_devaluation_cycle_closes_out_the_predecessor_on_publish(conn, issuer_id, tmp_path):
    with pytest.raises(_ForceRollback):
        with conn.transaction():
            result1, publish1 = _link_approve_publish_v1(conn, tmp_path)
            assert publish1.superseded_version_id is None

            result2 = link_bundle(_bundle(rate=0.02, effective_from="2026-06-01", source_url=SOURCE_URL_V2), conn, new_version=True)
            _approve_everything(conn, result2.card_version_id)
            golden2 = _write_golden(tmp_path, 0.02, "v2.json")

            publish2 = publish_card_version(conn, result2.card_version_id, [golden2])
            assert publish2.superseded_version_id == result1.card_version_id

            with conn.cursor() as cur:
                cur.execute("select status, effective_to from card_versions where id = %s", (result1.card_version_id,))
                v1_status, v1_effective_to = cur.fetchone()
                assert v1_status == "published"  # still published, per SS I.6 -- both remain queryable
                assert v1_effective_to.isoformat() == "2026-05-31"  # one day before v2's effective_from

                cur.execute("select status, effective_to from card_versions where id = %s", (result2.card_version_id,))
                v2_status, v2_effective_to = cur.fetchone()
                assert v2_status == "published"
                assert v2_effective_to is None  # open-ended -- the current live version

                # confirms current_card_versions resolves to exactly v2, not both/neither
                # (current_card_versions filters on current_date, which this test doesn't
                # control -- both v1/v2's effective_from are in the past relative to whenever
                # this test runs, and v1's effective_to is now also in the past, so only v2
                # should ever show up here regardless of the real wall-clock date)
                cur.execute(
                    "select cv.id::text from cards c join current_card_versions cv on cv.card_id = c.id where c.key = %s",
                    (CARD_KEY,),
                )
                assert [r[0] for r in cur.fetchall()] == [result2.card_version_id]

            raise _ForceRollback()

    # proof the rollback genuinely undid both publishes -- normal (non-published) cleanup can proceed
    with conn.cursor() as cur:
        cur.execute("select count(*) from cards where key = %s", (CARD_KEY,))
        assert cur.fetchone()[0] == 0
