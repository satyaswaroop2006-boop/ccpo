"""FastAPI TestClient tests for `POST /card-versions/{id}/publish` (Part
F §F.2.3/§F.4, Slice 7, docs/DECISIONS.md #170). Same live-DB,
`zz_test_`-prefixed fixture discipline as `tests/test_api_review_
queue.py`, PLUS `tests/test_ingest_publish.py`'s own extra concern:
publishing is IRREVERSIBLE (Part D Decision 2), so the success path
must never leave a permanent row behind.

The SAVEPOINT/force-rollback trick `test_ingest_publish.py` uses works
there because the test calls `publish_card_version(conn, ...)` directly
with its OWN `conn`. An HTTP round trip through `TestClient` would
normally use a DIFFERENT connection (a fresh one from `get_ingest_
connection`'s own `psycopg.connect()`) -- so instead, `get_ingest_
connection` is overridden to yield the TEST's own `conn` (not a new
one), letting the test wrap the `client.post(...)` call inside its own
outer `conn.transaction()` and force it to roll back afterward, exactly
as if the endpoint's own internal transaction were a SAVEPOINT under
the test's. This depends on `app/main.py`'s endpoints using `conn.
transaction()` rather than a bare `with conn:` (the latter would
`commit()` AND `close()` the connection directly, defeating this
entirely) -- a real fix made while building this test file, not
assumed to already be true.
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
from fastapi.testclient import TestClient  # noqa: E402

from app.auth import AdminSession, get_admin_session  # noqa: E402
from app.main import app, get_ingest_connection  # noqa: E402
from ingest.link import link_bundle  # noqa: E402

ISSUER_KEY = "zz_test_api_publish_issuer"
CARD_KEY = "zz_test_api_publish_card"
CURRENCY_KEY = "zz_test_api_publish_currency"
SOURCE_URL = "https://example.test/zz-api-publish-mitc.pdf"

client = TestClient(app)


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
            (ISSUER_KEY, "ZZ Test API Publish Issuer", "bank"),
        )
        iid = cur.fetchone()[0]
    conn.commit()
    return iid


# Hand-computed: grocery ticket 700, 700*1%=7.00 exact. gross =
# 1,20,000*0.01 = 1,200.00. No caps/thresholds -> fee unwaived: steady_fee
# = 500*1.18=590.00. NACV steady = 1,200.00-590.00=610.00.
_MATCHING_GOLDEN = {
    "spend_annual": {"grocery": 120000},
    "expected": {"gross_reward_value": 1200.00, "fee_paid": 590.00, "nacv_steady_state": 610.00},
    "tolerance_rupees": 0.01,
}


def _bundle():
    return {
        "issuer_key": ISSUER_KEY, "key": CARD_KEY, "name": "ZZ Test API Publish Card", "network": "visa",
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


def _approve_everything(conn, card_version_id) -> None:
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
    conn.commit()


def _link_ready_to_publish(conn, tmp_path: Path):
    bundle = _bundle()
    bundle_path = tmp_path / "bundle.json"
    bundle_path.write_text(json.dumps(bundle))
    golden_path = tmp_path / "golden.json"
    golden_path.write_text(json.dumps(_MATCHING_GOLDEN))

    result = link_bundle(bundle, conn, bundle_path=str(bundle_path), golden_paths=[str(golden_path)])
    _approve_everything(conn, result.card_version_id)
    return result


@pytest.fixture
def as_admin():
    app.dependency_overrides[get_admin_session] = lambda: AdminSession(email="satya@example.test")
    yield
    del app.dependency_overrides[get_admin_session]


@pytest.fixture
def shared_conn(conn):
    """Overrides `get_ingest_connection` to hand the ENDPOINT the test's
    own `conn`, instead of opening a fresh one -- see this module's own
    docstring for why that's required to test publish's success path
    safely."""
    def _get():
        yield conn
    app.dependency_overrides[get_ingest_connection] = _get
    yield
    del app.dependency_overrides[get_ingest_connection]


def test_publish_endpoint_requires_authentication(conn, issuer_id, tmp_path):
    result = _link_ready_to_publish(conn, tmp_path)
    response = client.post(f"/card-versions/{result.card_version_id}/publish", json={"confirm_card_key": CARD_KEY})
    assert response.status_code == 401


def test_publish_endpoint_refuses_when_confirmation_key_does_not_match(conn, issuer_id, tmp_path, as_admin, shared_conn):
    result = _link_ready_to_publish(conn, tmp_path)
    response = client.post(f"/card-versions/{result.card_version_id}/publish", json={"confirm_card_key": "wrong_key"})
    assert response.status_code == 422
    assert "does not match" in response.json()["detail"]

    with conn.cursor() as cur:
        cur.execute("select status from card_versions where id = %s", (result.card_version_id,))
        assert cur.fetchone()[0] == "draft"


def test_publish_endpoint_refuses_when_not_ready_and_reports_why(conn, issuer_id, tmp_path, as_admin, shared_conn):
    """Fresh link -- source_links are all 'unreviewed', so the gate fails
    before publish_card_version is even reached (no crash on the missing
    bundle_path/golden_paths path either, since both ARE set here)."""
    bundle = _bundle()
    bundle_path = tmp_path / "bundle.json"
    bundle_path.write_text(json.dumps(bundle))
    result = link_bundle(bundle, conn, bundle_path=str(bundle_path), golden_paths=["unused.json"])

    response = client.post(f"/card-versions/{result.card_version_id}/publish", json={"confirm_card_key": CARD_KEY})
    assert response.status_code == 422
    assert "not approved" in response.json()["detail"]

    with conn.cursor() as cur:
        cur.execute("select status from card_versions where id = %s", (result.card_version_id,))
        assert cur.fetchone()[0] == "draft"


def test_publish_endpoint_refuses_cleanly_when_bundle_path_and_golden_paths_are_unset(conn, issuer_id, as_admin, shared_conn):
    """Exactly the case migrations 0003/0004 exist for -- a card_version
    with neither recorded refuses with a clean 422 (via check_publish_
    gate) rather than crashing inside publish_card_version on a `None`
    bundle_path."""
    result = link_bundle(_bundle(), conn)  # no bundle_path/golden_paths given
    _approve_everything(conn, result.card_version_id)

    response = client.post(f"/card-versions/{result.card_version_id}/publish", json={"confirm_card_key": CARD_KEY})
    assert response.status_code == 422
    detail = response.json()["detail"]
    assert "no bundle_path recorded" in detail
    assert "no golden_paths recorded" in detail


def test_publish_endpoint_unknown_card_version_returns_404(as_admin, shared_conn):
    response = client.post(
        "/card-versions/00000000-0000-0000-0000-000000000000/publish", json={"confirm_card_key": "whatever"},
    )
    assert response.status_code == 404


def test_publish_endpoint_succeeds_and_flips_status_without_leaving_a_permanent_published_row(
    conn, issuer_id, tmp_path, as_admin, shared_conn,
):
    result = _link_ready_to_publish(conn, tmp_path)

    class _ForceRollback(Exception):
        pass

    with pytest.raises(_ForceRollback):
        with conn.transaction():  # the endpoint's own conn.transaction() nests as a SAVEPOINT under this
            response = client.post(
                f"/card-versions/{result.card_version_id}/publish", json={"confirm_card_key": CARD_KEY},
            )
            assert response.status_code == 200, response.text
            body = response.json()
            assert body["card_key"] == CARD_KEY
            assert body["card_version_id"] == result.card_version_id
            assert body["superseded_version_id"] is None
            assert len(body["scenario_results"]) == 1
            assert body["scenario_results"][0]["passed"] is True

            with conn.cursor() as cur:
                cur.execute("select status, published_at from card_versions where id = %s", (result.card_version_id,))
                status, published_at = cur.fetchone()
                assert status == "published"
                assert published_at is not None

            raise _ForceRollback()

    # Proof the rollback genuinely worked -- same pattern as
    # tests/test_ingest_publish.py's own success-path test.
    with conn.cursor() as cur:
        cur.execute("select status from card_versions where id = %s", (result.card_version_id,))
        assert cur.fetchone()[0] == "draft"
