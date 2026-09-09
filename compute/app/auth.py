"""Part F §F.6's compute-side admin allow-list check (Slice 6, docs/
DECISIONS.md #168) for the auth-gated endpoints (`GET /review-queue`,
`POST /source-links/{id}/approve`/`/reject`; `/card-versions/{id}/publish`
is Slice 7).

F.6 is explicit that "the four auth-gated endpoints (F.3) check the
verified session's user id against a single allow-listed value" -- that
sentence is about THESE endpoints, not just `web/`'s own `lib/auth.ts`
(already built, Slice 5). A Next.js-only check would mean anyone who can
reach this service directly -- not through the Next.js app -- bypasses
auth entirely; `web/`'s check stays as a fast, user-facing gate (redirect
before a button even renders), this module is the real one.

Verifies the caller's bearer token against Supabase's own Auth API
(`GET /auth/v1/user`) rather than decoding the JWT locally -- confirmed
directly against the live project, not assumed: no JWT secret or JWKS
handling needed here, reuses `SUPABASE_URL`/`SUPABASE_SERVICE_ROLE_KEY`
already configured for `ingest/storage.py`. One network call per
request -- immaterial at this endpoint's traffic (a single admin,
reviewing a handful of source_links at a time), same "not worth caching
away" posture `app/main.py::_partition_universe` already takes for its
own per-request probe.
"""
from __future__ import annotations

import os
from dataclasses import dataclass

import httpx
from fastapi import Header, HTTPException


@dataclass(frozen=True)
class AdminSession:
    email: str


def get_admin_session(authorization: str | None = Header(default=None)) -> AdminSession:
    """FastAPI dependency -- raises `HTTPException` (401/403/500) rather
    than returning `None` on failure, so a route that forgets to check a
    falsy return value can't accidentally proceed unauthenticated; every
    route using this as a `Depends` either gets a real `AdminSession` or
    the request never reaches the route body at all."""
    allowed_email = os.environ.get("ADMIN_ALLOWED_EMAIL")
    if not allowed_email:
        raise HTTPException(
            status_code=500,
            detail="ADMIN_ALLOWED_EMAIL is not configured -- refusing to evaluate admin access.",
        )

    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="missing bearer token")
    token = authorization.split(" ", 1)[1].strip()
    if not token:
        raise HTTPException(status_code=401, detail="missing bearer token")

    base_url = os.environ.get("SUPABASE_URL")
    service_role_key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
    if not base_url or not service_role_key:
        raise HTTPException(
            status_code=500,
            detail="SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY not configured -- cannot verify a session.",
        )

    try:
        resp = httpx.get(
            f"{base_url}/auth/v1/user",
            headers={"Authorization": f"Bearer {token}", "apikey": service_role_key},
            timeout=10.0,
        )
    except httpx.HTTPError as e:
        raise HTTPException(status_code=502, detail=f"could not verify session with Supabase: {e}")

    # Supabase's Auth API returns 401/403 (the exact code varies by failure
    # reason -- expired, malformed, revoked) for any invalid token; treated
    # uniformly here as "not authenticated" rather than trying to
    # distinguish reasons the caller has no legitimate use for.
    if resp.status_code != 200:
        raise HTTPException(status_code=401, detail="invalid or expired session")

    email = (resp.json().get("email") or "").strip()
    if not email or email.lower() != allowed_email.lower():
        raise HTTPException(status_code=403, detail="not authorized")

    return AdminSession(email=email)
