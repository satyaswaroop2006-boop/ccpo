"""FastAPI TestClient tests for `GET /review-queue` and `POST /source-
links/{id}/approve`/`/reject` (Part F §F.2.3/§F.3, Slice 6, docs/
DECISIONS.md #168). Live-DB integration tests, same `zz_test_`-prefixed
fixture discipline as `tests/test_ingest_review.py` -- these endpoints
are thin wrappers over exactly that module's own query/tables, so
there's nothing meaningful to fake here; skipped entirely when
`DATABASE_URL` isn't set or reachable.

`get_admin_session` is overridden to a fixed `AdminSession` (same
`app.dependency_overrides` pattern `tests/test_api_evaluate.py` already
uses for `get_repository`) -- the allow-list/JWT-verification logic
itself is `app/auth.py`'s own concern, already covered by `tests/
test_app_auth.py`; these tests exercise the review-queue/approve/reject
logic, not auth a second time. One test explicitly does NOT override
the dependency, to prove the route is unreachable without it.
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
from fastapi.testclient import TestClient  # noqa: E402

from app.auth import AdminSession, get_admin_session  # noqa: E402
from app.main import app  # noqa: E402
from ingest.link import link_bundle  # noqa: E402

ISSUER_KEY = "zz_test_api_review_issuer"
CARD_KEY = "zz_test_api_review_card"
CURRENCY_KEY = "zz_test_api_review_currency"
SOURCE_URL = "https://example.test/zz-api-review-mitc.pdf"

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
def linked(conn):
    with conn.cursor() as cur:
        cur.execute(
            "insert into issuers (key, name, issuer_type) values (%s,%s,%s) returning id",
            (ISSUER_KEY, "ZZ Test API Review Issuer", "bank"),
        )
    conn.commit()

    bundle = {
        "issuer_key": ISSUER_KEY, "key": CARD_KEY, "name": "ZZ Test API Review Card", "network": "visa",
        "currency": CURRENCY_KEY, "effective_from": "2026-01-01",
        "sources": {"src1": {
            "url": SOURCE_URL, "source_type": "mitc", "title": "ZZ Test API Review MITC",
            "storage_path": "sources/zz_test_api_review/src1.pdf", "captured_at": "2026-01-01",
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
    return link_bundle(bundle, conn)


@pytest.fixture
def as_admin():
    app.dependency_overrides[get_admin_session] = lambda: AdminSession(email="satya@example.test")
    yield
    del app.dependency_overrides[get_admin_session]


def test_review_queue_requires_authentication():
    response = client.get("/review-queue")
    assert response.status_code == 401


def test_review_queue_lists_the_new_cards_source_links_with_drafted_fields(conn, linked, as_admin):
    response = client.get("/review-queue")
    assert response.status_code == 200
    groups = {g["label"]: g for g in response.json()["groups"]}

    card_group = groups[f"card:{CARD_KEY}"]
    assert card_group["card_version_id"] == linked.card_version_id
    earning_rule_item = next(i for i in card_group["items"] if i["entity_type"] == "earning_rule")
    assert earning_rule_item["entity_fields"]["key"] == "base"
    assert earning_rule_item["source_title"] == "ZZ Test API Review MITC"
    # storage_path has no real object behind it in this fixture, so signing
    # fails and degrades to None rather than a broken link (app/main.py::
    # _signed_snapshot_url) -- asserted explicitly, not left unchecked.
    assert earning_rule_item["source_snapshot_url"] is None

    currency_group = groups[f"issuer:{ISSUER_KEY} (shared currency)"]
    assert {i["entity_type"] for i in currency_group["items"]} == {"reward_currency", "redemption_route"}


def test_approve_then_reject_flip_reviewer_status_and_narrow_columns_only(conn, linked, as_admin):
    with conn.cursor() as cur:
        cur.execute(
            "select id from source_links where entity_type = 'earning_rule' and entity_id in"
            " (select id from earning_rules where card_version_id = %s)",
            (linked.card_version_id,),
        )
        earning_rule_link_id = str(cur.fetchone()[0])
        cur.execute(
            "select id from source_links where entity_type = 'card_version' and entity_id = %s",
            (linked.card_version_id,),
        )
        card_version_link_id = str(cur.fetchone()[0])

    approve_resp = client.post(f"/source-links/{earning_rule_link_id}/approve")
    assert approve_resp.status_code == 200
    assert approve_resp.json() == {"source_link_id": earning_rule_link_id, "reviewer_status": "approved"}

    reject_resp = client.post(f"/source-links/{card_version_link_id}/reject", json={"note": "wrong document cited"})
    assert reject_resp.status_code == 200
    assert reject_resp.json() == {"source_link_id": card_version_link_id, "reviewer_status": "rejected"}

    with conn.cursor() as cur:
        cur.execute("select reviewer_status, previous_rule_note, source_id from source_links where id = %s", (earning_rule_link_id,))
        status, note, source_id = cur.fetchone()
        assert status == "approved"
        assert note is None  # approve never touches previous_rule_note
        cur.execute("select id from sources where url = %s", (SOURCE_URL,))
        assert source_id == cur.fetchone()[0]  # source_id untouched -- docs/DECISIONS.md #158's own corruption class

        cur.execute("select reviewer_status, previous_rule_note from source_links where id = %s", (card_version_link_id,))
        status, note = cur.fetchone()
        assert status == "rejected"
        assert note == "wrong document cited"

    # both now reviewed -- the card group disappears from the queue entirely
    # (mirrors tests/test_ingest_review.py's own empty-state assertion)
    response = client.get("/review-queue")
    labels = {g["label"] for g in response.json()["groups"]}
    assert f"card:{CARD_KEY}" not in labels


def test_reject_without_a_note_is_rejected_at_the_schema_boundary(conn, linked, as_admin):
    with conn.cursor() as cur:
        cur.execute(
            "select id from source_links where entity_type = 'card_version' and entity_id = %s",
            (linked.card_version_id,),
        )
        card_version_link_id = str(cur.fetchone()[0])

    response = client.post(f"/source-links/{card_version_link_id}/reject", json={"note": ""})
    assert response.status_code == 422

    with conn.cursor() as cur:
        cur.execute("select reviewer_status from source_links where id = %s", (card_version_link_id,))
        assert cur.fetchone()[0] == "unreviewed"  # rejected at validation, never reached the UPDATE


def test_approve_unknown_source_link_id_returns_404(as_admin):
    response = client.post("/source-links/00000000-0000-0000-0000-000000000000/approve")
    assert response.status_code == 404
