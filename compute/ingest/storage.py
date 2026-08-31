"""Supabase Storage client for `ingest capture` (Part I SS I.1).

A separate service from the Postgres connection every other `ingest`/
`app` module uses -- Storage has its own REST API and its own
credentials (`SUPABASE_URL` + a `service_role` key), neither of which
existed anywhere in this project before this module (confirmed by
search: only `DATABASE_URL` was ever configured). Implemented directly
against Storage's REST API with `httpx` (already a dependency, via
FastAPI's TestClient) rather than adding the `supabase-py` client
library -- the surface this tool needs (ensure a bucket exists, upload
an object) is two endpoints, not worth a new SDK dependency for.

`StorageBackend` is a `Protocol` so tests never have to hit the real
Supabase project: `FakeStorageBackend` (in-memory) implements the same
interface for every non-live test; `SupabaseStorageBackend` is exercised
directly only by `tests/test_ingest_capture_storage.py`'s live,
skip-if-unreachable integration tests, same posture as `tests/test_
postgres_repository.py`'s own DB-live tests.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

import httpx


class StorageError(Exception):
    """Raised on any Storage API failure -- callers decide whether that's
    fatal or just a warning; this module never guesses."""


class StorageBackend(Protocol):
    def ensure_bucket(self, bucket: str) -> None: ...

    def upload(self, bucket: str, object_path: str, data: bytes, content_type: str) -> None: ...

    def exists(self, bucket: str, object_path: str) -> bool: ...


@dataclass(frozen=True)
class SupabaseStorageBackend:
    """`bucket` defaults to "sources" -- Part I SS I.2's own worked
    example paths (`"sources/example_bank/eb_ultra_mitc_2026.pdf"`) read
    naturally as bucket="sources", object_path="example_bank/....pdf",
    so that's the convention this module follows rather than inventing
    a different one. Buckets created here are PRIVATE (not public) --
    matches this project's existing RLS posture (Part D Decision 9:
    catalog tables are world-readable but writes are service-role only;
    source documents are evidentiary/internal, not meant for direct
    public URLs)."""

    base_url: str  # SUPABASE_URL, e.g. "https://xxxx.supabase.co"
    service_role_key: str
    timeout: float = 30.0

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.service_role_key}",
            "apikey": self.service_role_key,
        }

    def _bucket_not_found(self, resp: httpx.Response) -> bool:
        """Supabase's Storage API wraps a missing-bucket lookup as HTTP
        400 (not 404) with the real status in the JSON body's own
        `statusCode` field (`{"statusCode":"404","error":"Bucket not
        found",...}`) -- confirmed against the live API, not documented
        anywhere obvious; a literal `resp.status_code == 404` check
        never fires. Falls back to a literal 404 too, in case that ever
        changes or a different Storage deployment behaves differently."""
        if resp.status_code == 404:
            return True
        if resp.status_code == 400:
            try:
                body = resp.json()
            except Exception:
                return False
            return str(body.get("statusCode")) == "404" or body.get("error") == "Bucket not found"
        return False

    def ensure_bucket(self, bucket: str) -> None:
        with httpx.Client(timeout=self.timeout) as client:
            resp = client.get(f"{self.base_url}/storage/v1/bucket/{bucket}", headers=self._headers())
            if resp.status_code == 200:
                return
            if not self._bucket_not_found(resp):
                raise StorageError(f"checking bucket {bucket!r} failed: {resp.status_code} {resp.text}")
            resp = client.post(
                f"{self.base_url}/storage/v1/bucket", headers=self._headers(),
                json={"name": bucket, "public": False},
            )
            if resp.status_code not in (200, 201):
                raise StorageError(f"creating bucket {bucket!r} failed: {resp.status_code} {resp.text}")

    def upload(self, bucket: str, object_path: str, data: bytes, content_type: str) -> None:
        with httpx.Client(timeout=self.timeout) as client:
            resp = client.post(
                f"{self.base_url}/storage/v1/object/{bucket}/{object_path}",
                headers={**self._headers(), "Content-Type": content_type, "x-upsert": "true"},
                content=data,
            )
            if resp.status_code not in (200, 201):
                raise StorageError(f"uploading {bucket}/{object_path} failed: {resp.status_code} {resp.text}")

    def exists(self, bucket: str, object_path: str) -> bool:
        with httpx.Client(timeout=self.timeout) as client:
            resp = client.get(
                f"{self.base_url}/storage/v1/object/info/{bucket}/{object_path}", headers=self._headers(),
            )
            return resp.status_code == 200


@dataclass
class FakeStorageBackend:
    """In-memory stand-in for tests -- never touches the network. Records
    every upload so a test can assert on what would have been sent."""

    objects: dict[tuple[str, str], bytes] | None = None
    buckets_ensured: list[str] | None = None

    def __post_init__(self) -> None:
        if self.objects is None:
            self.objects = {}
        if self.buckets_ensured is None:
            self.buckets_ensured = []

    def ensure_bucket(self, bucket: str) -> None:
        if bucket not in self.buckets_ensured:
            self.buckets_ensured.append(bucket)

    def upload(self, bucket: str, object_path: str, data: bytes, content_type: str) -> None:
        self.objects[(bucket, object_path)] = data

    def exists(self, bucket: str, object_path: str) -> bool:
        return (bucket, object_path) in self.objects
