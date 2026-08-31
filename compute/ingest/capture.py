"""Part I SS I.1's CAPTURE stage / `ingest capture` tool.

Fetches (or accepts a manually-downloaded copy of) each source a bundle
declares, verifies a PDF's completeness, stores the bytes in Supabase
Storage, and records `storage_path`/`captured_at` back onto the bundle's
own source entry -- turning "a bare URL" into "a snapshot", per SS I.1's
own words. Runs BEFORE `ingest lint` in the pipeline (CAPTURE -> DRAFT ->
LINT -> ...) and needs no database access on its own -- it operates on
the bundle FILE. `--sync-db` is the one optional step that also touches
Postgres, for a source that was already `ingest link`ed before this tool
existed (CASHBACK SBI's own situation, docs/DECISIONS.md #135/#143/#144)
-- updating `storage_path`/`captured_at` on an existing `sources` row,
confirmed safe against an already-published card_version because
`sources`/`source_links` carry no immutability trigger (Part D Decision
2's guarantee is scoped to `card_versions` and its six child rule
tables; `sources` is a separate, unguarded table -- verified directly
against `0001_init.sql`'s trigger-attachment list, not assumed).

Two distinct failure modes this module treats differently on purpose:

- **Fetch failure** (network error, non-200, or a `.pdf` URL that comes
  back as something that isn't a PDF -- the exact "bot-detection
  rejection" shape a real issuer page can return) is NOT fatal to the
  whole run: it raises `CaptureError` naming exactly what to do next --
  supply `--file <key>=<path>` with an already-downloaded copy. Capture
  is fetch-OR-accept-upload, never fetch-only.
- **A PDF that parses but doesn't match its own stated page count** is a
  WARNING, not a hard failure -- the snapshot is still stored (partial
  evidence beats no evidence) but flagged loudly, in the CLI output and
  written onto the bundle's source entry (`_capture_warning`), so it can
  never be silently mistaken for a complete capture later. A PDF that
  fails to PARSE AT ALL is a hard failure instead -- an unparseable
  "snapshot" is worse than no snapshot (it would satisfy `ingest lint`'s
  own completeness check while being useless as evidence).
"""
from __future__ import annotations

import datetime as _dt
import json
import re
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import Callable

import httpx
from pypdf import PdfReader

from ingest.bundle import declared_sources
from ingest.storage import StorageBackend

_PDF_MAGIC = b"%PDF-"
_PAGE_OF_TOTAL_RE = re.compile(r"page\s+\d+\s+of\s+(\d+)", re.IGNORECASE)
_DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)
_BUCKET = "sources"


class CaptureError(Exception):
    """Refuses loudly, naming exactly what's wrong and what to do about
    it -- same posture as every other `ingest` tool's error type."""


@dataclass(frozen=True)
class FetchResult:
    ok: bool
    content: bytes | None
    content_type: str | None
    error: str | None


Fetcher = Callable[[str], FetchResult]


def fetch_source(url: str, client: httpx.Client | None = None) -> FetchResult:
    owns_client = client is None
    client = client or httpx.Client(timeout=30.0, follow_redirects=True, headers={"User-Agent": _DEFAULT_UA})
    try:
        try:
            resp = client.get(url)
        except httpx.HTTPError as e:
            return FetchResult(ok=False, content=None, content_type=None, error=f"request failed: {type(e).__name__}: {e}")
    finally:
        if owns_client:
            client.close()

    if resp.status_code != 200:
        return FetchResult(
            ok=False, content=None, content_type=resp.headers.get("content-type"),
            error=f"HTTP {resp.status_code} -- possible bot-detection or access block",
        )

    declared_length = resp.headers.get("content-length")
    if declared_length is not None and len(resp.content) < int(declared_length):
        return FetchResult(
            ok=False, content=None, content_type=resp.headers.get("content-type"),
            error=f"response truncated in transit: server declared {declared_length} bytes, got {len(resp.content)}",
        )

    looks_like_pdf_url = url.lower().split("?")[0].endswith(".pdf")
    if looks_like_pdf_url and not resp.content.startswith(_PDF_MAGIC):
        return FetchResult(
            ok=False, content=None, content_type=resp.headers.get("content-type"),
            error="URL ends in .pdf but the response body isn't a PDF (no %PDF- header) -- "
                  "likely a bot-wall or error page returned instead of the document",
        )

    return FetchResult(ok=True, content=resp.content, content_type=resp.headers.get("content-type"), error=None)


@dataclass(frozen=True)
class PdfVerification:
    parsed_ok: bool
    page_count: int | None
    declared_total: int | None
    mismatch: bool
    note: str


def verify_pdf(data: bytes) -> PdfVerification:
    try:
        reader = PdfReader(BytesIO(data))
        page_count = len(reader.pages)
    except Exception as e:
        return PdfVerification(
            parsed_ok=False, page_count=None, declared_total=None, mismatch=True,
            note=f"PDF failed to parse ({type(e).__name__}: {e}) -- likely a truncated or corrupted download",
        )

    declared_totals: set[int] = set()
    for page in reader.pages:
        try:
            text = page.extract_text() or ""
        except Exception:
            continue
        declared_totals.update(int(m.group(1)) for m in _PAGE_OF_TOTAL_RE.finditer(text))

    if not declared_totals:
        return PdfVerification(
            parsed_ok=True, page_count=page_count, declared_total=None, mismatch=False,
            note=f"parsed {page_count} page(s); document does not self-declare a total page count anywhere",
        )

    # Multiple different "Page X of Y" totals (e.g. an appendix with its own
    # numbering) -- don't guess which is authoritative; compare against the
    # largest, the conservative direction (less likely to falsely flag a match).
    declared_total = max(declared_totals)
    mismatch = page_count != declared_total
    note = (
        f"parsed {page_count} page(s); document states {declared_total} -- MISMATCH, likely truncated"
        if mismatch else
        f"parsed {page_count} page(s), matching the document's own stated total ({declared_total})"
    )
    return PdfVerification(parsed_ok=True, page_count=page_count, declared_total=declared_total, mismatch=mismatch, note=note)


def _extension_for(url: str, content_type: str | None, data: bytes) -> str:
    if data.startswith(_PDF_MAGIC):
        return "pdf"
    tail = url.lower().split("?")[0].rsplit("/", 1)[-1]
    if "." in tail:
        return tail.rsplit(".", 1)[-1]
    if content_type and "html" in content_type:
        return "html"
    return "bin"


@dataclass(frozen=True)
class CaptureResult:
    source_key: str
    url: str
    method: str  # "fetched" | "manual"
    storage_path: str
    captured_at: str  # ISO date
    pdf: PdfVerification | None
    warnings: tuple[str, ...]


def capture_source(
    storage: StorageBackend,
    issuer_key: str,
    source_key: str,
    source: dict,
    manual_file: Path | None = None,
    fetcher: Fetcher = fetch_source,
    today: str | None = None,
) -> CaptureResult:
    url = source["url"]
    warnings: list[str] = []

    if manual_file is not None:
        data = manual_file.read_bytes()
        content_type = None
        method = "manual"
    else:
        result = fetcher(url)
        if not result.ok:
            raise CaptureError(
                f"{source_key}: fetch failed -- {result.error}. "
                f"Supply a manually-downloaded copy with --file {source_key}=<path>."
            )
        data = result.content
        content_type = result.content_type
        method = "fetched"

    pdf_verification = None
    if data.startswith(_PDF_MAGIC):
        pdf_verification = verify_pdf(data)
        if not pdf_verification.parsed_ok:
            raise CaptureError(
                f"{source_key}: {pdf_verification.note}. "
                f"Supply a manually-downloaded copy with --file {source_key}=<path>."
            )
        if pdf_verification.mismatch:
            warnings.append(pdf_verification.note)

    captured_at = today or _dt.date.today().isoformat()
    ext = _extension_for(url, content_type, data)
    object_path = f"{issuer_key}/{source_key}-{captured_at}.{ext}"

    storage.ensure_bucket(_BUCKET)
    storage.upload(_BUCKET, object_path, data, content_type or "application/octet-stream")

    return CaptureResult(
        source_key=source_key, url=url, method=method,
        storage_path=f"{_BUCKET}/{object_path}", captured_at=captured_at,
        pdf=pdf_verification, warnings=tuple(warnings),
    )


def capture_bundle(
    bundle_path: Path,
    storage: StorageBackend,
    source_keys: list[str] | None = None,
    manual_files: dict[str, Path] | None = None,
    force: bool = False,
    fetcher: Fetcher = fetch_source,
    today: str | None = None,
) -> list[CaptureResult]:
    """Mutates and re-writes `bundle_path` in place -- every captured
    source's `storage_path`/`captured_at` (and `_capture_warning`, if a
    PDF mismatch was found) are written back onto its entry, exactly the
    same file `ingest lint`/`ingest link` read afterward. The JSON is
    re-serialized (`json.dumps(..., indent=2)`), which reflows array
    formatting -- a real, visible diff beyond just the changed fields,
    called out here rather than left as a surprise; nothing is lost,
    only whitespace changes."""
    bundle = json.loads(bundle_path.read_text())
    sources = declared_sources(bundle)
    if not sources:
        raise CaptureError(f"{bundle_path}: no sources/_sources block declared")

    issuer_key = bundle.get("issuer_key")
    if not issuer_key:
        raise CaptureError(f"{bundle_path}: bundle has no issuer_key -- needed to build the storage path")

    manual_files = manual_files or {}
    keys = source_keys or list(sources.keys())

    results: list[CaptureResult] = []
    for key in keys:
        if key not in sources:
            raise CaptureError(f"source key {key!r} not declared in this bundle's sources/_sources block")
        source = sources[key]
        already_captured = bool(source.get("storage_path")) and bool(source.get("captured_at"))
        if already_captured and not force:
            continue  # idempotent -- SS I.7's re-verification is a --force re-run, not the default

        result = capture_source(storage, issuer_key, key, source, manual_files.get(key), fetcher=fetcher, today=today)
        source["storage_path"] = result.storage_path
        source["captured_at"] = result.captured_at
        if result.warnings:
            source["_capture_warning"] = "; ".join(result.warnings)
        elif "_capture_warning" in source:
            del source["_capture_warning"]  # a --force re-run that's now clean shouldn't keep a stale warning
        results.append(result)

    if results:
        bundle_path.write_text(json.dumps(bundle, indent=2) + "\n")

    return results


def sync_captured_sources_to_db(conn, bundle: dict) -> list[str]:
    """For a source that was `ingest link`ed BEFORE this tool existed
    (CASHBACK SBI's own situation) -- pushes a just-captured storage_
    path/captured_at into the matching live `sources` row, matched by
    URL (the same dedup key `ingest link` itself already uses). A source
    with no matching DB row (the normal, forward-looking case: capture
    runs before link ever does) is silently skipped, not an error."""
    sources = declared_sources(bundle)
    updated: list[str] = []
    with conn.cursor() as cur:
        for key, source in sources.items():
            if not (source.get("storage_path") and source.get("captured_at")):
                continue
            cur.execute(
                "update sources set storage_path = %s, captured_at = %s, last_checked_at = now()"
                " where url = %s returning id",
                (source["storage_path"], source["captured_at"], source["url"]),
            )
            if cur.fetchone() is not None:
                updated.append(key)
    conn.commit()
    return updated
