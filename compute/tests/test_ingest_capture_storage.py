"""Live integration tests for `SupabaseStorageBackend` (Part I SS I.1,
docs/DECISIONS.md #144) -- the one piece of `ingest capture` that can't
be verified against a fake: does the real Storage REST API actually
accept an upload with these headers/URL shape. Skipped entirely when
`SUPABASE_URL`/`SUPABASE_SERVICE_ROLE_KEY` aren't set or the endpoint
isn't reachable, same posture as `tests/test_postgres_repository.py`'s
own DB-live tests. Every object this file creates uses a `zz_test_`
prefixed path and is deleted in the fixture's teardown -- the bucket
itself (`sources`, shared with real captures) is never deleted, only
the test's own objects within it.
"""
import os

import httpx
import pytest
from dotenv import load_dotenv

load_dotenv()
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_SERVICE_ROLE_KEY")

if SUPABASE_URL and SUPABASE_KEY:
    try:
        _probe = httpx.get(
            f"{SUPABASE_URL}/storage/v1/bucket",
            headers={"Authorization": f"Bearer {SUPABASE_KEY}", "apikey": SUPABASE_KEY},
            timeout=5.0,
        )
        SUPABASE_REACHABLE = _probe.status_code in (200, 400, 401, 403)  # reachable at all, auth aside
    except Exception:
        SUPABASE_REACHABLE = False
else:
    SUPABASE_REACHABLE = False

pytestmark = pytest.mark.skipif(not SUPABASE_REACHABLE, reason="SUPABASE_URL/SUPABASE_SERVICE_ROLE_KEY not set or not reachable")

from ingest.storage import StorageError, SupabaseStorageBackend  # noqa: E402

TEST_OBJECT_PATH = "zz_test_ingest_capture/storage_backend_smoke_test.txt"


@pytest.fixture
def backend():
    b = SupabaseStorageBackend(base_url=SUPABASE_URL, service_role_key=SUPABASE_KEY)
    b.ensure_bucket("sources")
    yield b
    # best-effort cleanup -- a stray test object doesn't corrupt anything,
    # but leaving the bucket clean is good hygiene for a shared resource
    try:
        with httpx.Client(timeout=10.0) as client:
            client.request(
                "DELETE", f"{SUPABASE_URL}/storage/v1/object/sources/{TEST_OBJECT_PATH}",
                headers={"Authorization": f"Bearer {SUPABASE_KEY}", "apikey": SUPABASE_KEY},
            )
    except Exception:
        pass


def test_ensure_bucket_is_idempotent(backend):
    backend.ensure_bucket("sources")  # already created by the fixture -- must not raise
    backend.ensure_bucket("sources")


def test_upload_then_exists_round_trips(backend):
    assert backend.exists("sources", TEST_OBJECT_PATH) is False
    backend.upload("sources", TEST_OBJECT_PATH, b"hello from ccpo's ingest capture test suite", "text/plain")
    assert backend.exists("sources", TEST_OBJECT_PATH) is True


def test_upload_with_upsert_overwrites_without_erroring(backend):
    backend.upload("sources", TEST_OBJECT_PATH, b"first version", "text/plain")
    backend.upload("sources", TEST_OBJECT_PATH, b"second version", "text/plain")  # must not raise on re-upload
    assert backend.exists("sources", TEST_OBJECT_PATH) is True


def test_bad_credentials_raise_storage_error():
    bad = SupabaseStorageBackend(base_url=SUPABASE_URL, service_role_key="not-a-real-key")
    with pytest.raises(StorageError):
        bad.upload("sources", TEST_OBJECT_PATH, b"x", "text/plain")


def test_create_signed_url_returns_a_working_link_to_the_uploaded_object(backend):
    """Part F §F.2.3's "at minimum a link to the stored snapshot" (Slice 6,
    docs/DECISIONS.md #168) -- verifies the FULL round trip against the
    live API, not just that a URL-shaped string comes back: sign it, then
    actually fetch it and confirm the uploaded bytes are what's served."""
    backend.upload("sources", TEST_OBJECT_PATH, b"signed url smoke test content", "text/plain")
    url = backend.create_signed_url("sources", TEST_OBJECT_PATH, expires_in=60)
    assert url.startswith(SUPABASE_URL)
    with httpx.Client(timeout=10.0) as client:
        resp = client.get(url)
    assert resp.status_code == 200
    assert resp.content == b"signed url smoke test content"


def test_create_signed_url_for_missing_object_raises_storage_error(backend):
    with pytest.raises(StorageError):
        backend.create_signed_url("sources", "zz_test_ingest_capture/does-not-exist.txt", expires_in=60)
