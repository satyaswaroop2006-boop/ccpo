"""Tests for `app/auth.py::get_admin_session` (Part F §F.6, Slice 6,
docs/DECISIONS.md #168). The "missing header" / "misconfigured env"
paths are pure Python -- no network, always run. The "bogus token
rejected by Supabase" path is a live integration test against the real
Auth API (same skip-if-unreachable posture as `tests/test_postgres_
repository.py`), since that's the one thing that can't be verified
without the real service: does Supabase's own `/auth/v1/user` actually
reject what this module assumes it will.

No test exercises the "real, valid token" success path -- that needs an
actual signed-in session, which only exists via a real magic-link email
round trip (Slice 5's own end-to-end verification was done the same
way, manually, for the same reason). `tests/test_api_review_queue.py`
overrides this dependency entirely for its own endpoint tests rather
than mint a real token.
"""
import os

import httpx
import pytest
from dotenv import load_dotenv
from fastapi import HTTPException

load_dotenv()
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_SERVICE_ROLE_KEY")

if SUPABASE_URL and SUPABASE_KEY:
    try:
        _probe = httpx.get(f"{SUPABASE_URL}/auth/v1/user", headers={"apikey": SUPABASE_KEY}, timeout=5.0)
        SUPABASE_REACHABLE = True
    except Exception:
        SUPABASE_REACHABLE = False
else:
    SUPABASE_REACHABLE = False

from app.auth import get_admin_session  # noqa: E402


@pytest.fixture(autouse=True)
def _admin_email_configured(monkeypatch):
    monkeypatch.setenv("ADMIN_ALLOWED_EMAIL", "satya@example.test")


def test_missing_authorization_header_is_rejected_without_a_network_call():
    with pytest.raises(HTTPException) as exc_info:
        get_admin_session(authorization=None)
    assert exc_info.value.status_code == 401


def test_non_bearer_authorization_header_is_rejected():
    with pytest.raises(HTTPException) as exc_info:
        get_admin_session(authorization="Basic dXNlcjpwYXNz")
    assert exc_info.value.status_code == 401


def test_missing_admin_allowed_email_raises_500(monkeypatch):
    monkeypatch.delenv("ADMIN_ALLOWED_EMAIL", raising=False)
    with pytest.raises(HTTPException) as exc_info:
        get_admin_session(authorization="Bearer sometoken")
    assert exc_info.value.status_code == 500


@pytest.mark.skipif(not SUPABASE_REACHABLE, reason="SUPABASE_URL/SUPABASE_SERVICE_ROLE_KEY not set or not reachable")
def test_a_bogus_token_is_rejected_by_the_real_supabase_auth_api():
    """Confirms the live integration this module depends on, not just
    that our own code compiles: Supabase's `/auth/v1/user` genuinely
    returns non-200 for a malformed/unknown token."""
    with pytest.raises(HTTPException) as exc_info:
        get_admin_session(authorization="Bearer not-a-real-jwt")
    assert exc_info.value.status_code == 401
