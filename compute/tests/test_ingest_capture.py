"""Unit tests for `ingest capture` (Part I SS I.1, docs/DECISIONS.md
#144). No network, no database -- `fetch_source` is tested against an
`httpx.MockTransport` (real request/response shaping, zero real HTTP);
`capture_source`/`capture_bundle` are tested against `FakeStorageBackend`
(in-memory) with an injected fake fetcher. `verify_pdf`'s PDF-parsing
path is tested against a real, minimal PDF built with `pypdf.PdfWriter`
(no hand-crafted PDF bytes); its text-scanning path is tested by
mocking `PdfReader` directly, since `pypdf.PdfWriter` has no API to
draw actual text content onto a page (it's a manipulation library, not
an authoring one) -- decoupling "does the regex/mismatch logic work"
from "can this test construct a PDF with real extractable text" is
deliberate, not a shortcut around coverage.
"""
import json
from io import BytesIO
from pathlib import Path
from unittest.mock import patch

import httpx
import pytest
from pypdf import PdfWriter

from ingest.capture import (
    CaptureError,
    FetchResult,
    capture_bundle,
    capture_source,
    fetch_source,
    verify_pdf,
)
from ingest.storage import FakeStorageBackend

# ---------------------------------------------------------------------------
# fetch_source
# ---------------------------------------------------------------------------

def _client_with(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_fetch_source_succeeds_on_200():
    def handler(request):
        return httpx.Response(200, content=b"hello world", headers={"content-type": "text/html"})

    result = fetch_source("https://example.com/page.html", client=_client_with(handler))
    assert result.ok is True
    assert result.content == b"hello world"
    assert result.error is None


def test_fetch_source_reports_non_200_as_a_clear_error():
    def handler(request):
        return httpx.Response(403, content=b"forbidden")

    result = fetch_source("https://example.com/blocked.pdf", client=_client_with(handler))
    assert result.ok is False
    assert "403" in result.error
    assert "bot-detection" in result.error


def test_fetch_source_flags_transport_truncation_via_content_length():
    def handler(request):
        # declares more bytes than it actually sends -- the exact shape of a
        # connection cut off mid-transfer
        return httpx.Response(200, content=b"short", headers={"content-length": "999999"})

    result = fetch_source("https://example.com/big.pdf", client=_client_with(handler))
    assert result.ok is False
    assert "truncated in transit" in result.error


def test_fetch_source_flags_a_pdf_url_that_returns_non_pdf_content():
    """The exact "bot-detection rejection" shape: a .pdf URL that comes
    back as an HTML challenge/error page instead of the document."""
    def handler(request):
        return httpx.Response(200, content=b"<html>are you a robot?</html>", headers={"content-type": "text/html"})

    result = fetch_source("https://example.com/aurum-benefits.pdf", client=_client_with(handler))
    assert result.ok is False
    assert "bot-wall" in result.error


def test_fetch_source_accepts_a_genuine_pdf():
    def handler(request):
        return httpx.Response(200, content=b"%PDF-1.4\n...", headers={"content-type": "application/pdf"})

    result = fetch_source("https://example.com/mitc.pdf", client=_client_with(handler))
    assert result.ok is True


def test_fetch_source_reports_network_errors_without_crashing():
    def handler(request):
        raise httpx.ConnectTimeout("timed out", request=request)

    result = fetch_source("https://example.com/x.pdf", client=_client_with(handler))
    assert result.ok is False
    assert "request failed" in result.error


# ---------------------------------------------------------------------------
# verify_pdf
# ---------------------------------------------------------------------------

def _minimal_valid_pdf_bytes(n_pages: int) -> bytes:
    writer = PdfWriter()
    for _ in range(n_pages):
        writer.add_blank_page(width=200, height=200)
    buf = BytesIO()
    writer.write(buf)
    return buf.getvalue()


def test_verify_pdf_flags_unparseable_bytes():
    result = verify_pdf(b"%PDF-1.4\nthis is not a real pdf structure at all")
    assert result.parsed_ok is False
    assert result.page_count is None
    assert "truncated or corrupted" in result.note


def test_verify_pdf_reports_page_count_with_no_declared_total():
    result = verify_pdf(_minimal_valid_pdf_bytes(3))
    assert result.parsed_ok is True
    assert result.page_count == 3
    assert result.declared_total is None
    assert result.mismatch is False


def _fake_reader_with_page_texts(texts: list[str]):
    class _FakePage:
        def __init__(self, text):
            self._text = text

        def extract_text(self):
            return self._text

    class _FakeReader:
        def __init__(self, _data):
            self.pages = [_FakePage(t) for t in texts]

    return _FakeReader


def test_verify_pdf_matches_when_parsed_count_equals_declared_total():
    fake_reader_cls = _fake_reader_with_page_texts(["cover page", "Page 2 of 2"])
    with patch("ingest.capture.PdfReader", fake_reader_cls):
        result = verify_pdf(b"irrelevant-with-mock")
    assert result.parsed_ok is True
    assert result.page_count == 2
    assert result.declared_total == 2
    assert result.mismatch is False


def test_verify_pdf_flags_a_genuine_truncation_mismatch():
    """HDFC's real 49-page-MITC-came-back-partial shape: the document
    itself says "Page 12 of 49" but only 12 pages were actually parsed."""
    fake_reader_cls = _fake_reader_with_page_texts([f"Page {i} of 49" for i in range(1, 13)])
    with patch("ingest.capture.PdfReader", fake_reader_cls):
        result = verify_pdf(b"irrelevant-with-mock")
    assert result.parsed_ok is True
    assert result.page_count == 12
    assert result.declared_total == 49
    assert result.mismatch is True
    assert "MISMATCH" in result.note


def test_verify_pdf_takes_the_largest_total_when_multiple_are_seen():
    # e.g. a body ("Page 1 of 10") followed by an appendix with its own
    # numbering ("Page 1 of 3") -- don't guess which is authoritative,
    # compare against the largest (the conservative direction).
    fake_reader_cls = _fake_reader_with_page_texts(["Page 1 of 10", "Page 1 of 3"])
    with patch("ingest.capture.PdfReader", fake_reader_cls):
        result = verify_pdf(b"irrelevant-with-mock")
    assert result.declared_total == 10


# ---------------------------------------------------------------------------
# capture_source / capture_bundle
# ---------------------------------------------------------------------------

def _ok_fetcher(content=b"<html>not a pdf, just a generic fetched page</html>", content_type="text/html"):
    def fetcher(url):
        return FetchResult(ok=True, content=content, content_type=content_type, error=None)
    return fetcher


def _failing_fetcher(error="simulated failure"):
    def fetcher(url):
        return FetchResult(ok=False, content=None, content_type=None, error=error)
    return fetcher


def test_capture_source_fetches_and_stores():
    storage = FakeStorageBackend()
    result = capture_source(
        storage, issuer_key="sbi_card", source_key="reward_terms",
        source={"url": "https://example.com/reward-terms.pdf"},
        fetcher=_ok_fetcher(), today="2026-08-31",
    )
    assert result.method == "fetched"
    assert result.storage_path == "sources/sbi_card/reward_terms-2026-08-31.pdf"
    assert result.captured_at == "2026-08-31"
    assert storage.exists("sources", "sbi_card/reward_terms-2026-08-31.pdf")


def test_capture_source_raises_a_clear_error_on_fetch_failure():
    storage = FakeStorageBackend()
    with pytest.raises(CaptureError, match=r"fetch failed.*--file reward_terms="):
        capture_source(
            storage, issuer_key="sbi_card", source_key="reward_terms",
            source={"url": "https://example.com/reward-terms.pdf"},
            fetcher=_failing_fetcher("HTTP 403"), today="2026-08-31",
        )


def test_capture_source_accepts_a_manual_file_when_fetch_would_fail(tmp_path):
    manual = tmp_path / "manual.pdf"
    real_pdf_bytes = _minimal_valid_pdf_bytes(1)
    manual.write_bytes(real_pdf_bytes)
    storage = FakeStorageBackend()

    result = capture_source(
        storage, issuer_key="sbi_card", source_key="reward_terms",
        source={"url": "https://example.com/reward-terms.pdf"},
        manual_file=manual, fetcher=_failing_fetcher(), today="2026-08-31",
    )
    assert result.method == "manual"
    assert result.pdf.parsed_ok is True
    assert storage.objects[("sources", "sbi_card/reward_terms-2026-08-31.pdf")] == real_pdf_bytes


def test_capture_source_raises_on_an_unparseable_pdf():
    storage = FakeStorageBackend()
    with pytest.raises(CaptureError, match="truncated or corrupted"):
        capture_source(
            storage, issuer_key="sbi_card", source_key="reward_terms",
            source={"url": "https://example.com/reward-terms.pdf"},
            fetcher=_ok_fetcher(content=b"%PDF-1.4\nnot really a pdf"), today="2026-08-31",
        )


def test_capture_source_stores_but_warns_on_a_page_count_mismatch():
    valid_pdf = _minimal_valid_pdf_bytes(2)
    storage = FakeStorageBackend()
    fake_reader_cls = _fake_reader_with_page_texts(["Page 1 of 5", "Page 2 of 5"])
    with patch("ingest.capture.PdfReader", fake_reader_cls):
        result = capture_source(
            storage, issuer_key="sbi_card", source_key="reward_terms",
            source={"url": "https://example.com/reward-terms.pdf"},
            fetcher=_ok_fetcher(content=valid_pdf), today="2026-08-31",
        )
    assert result.warnings != ()
    assert "MISMATCH" in result.warnings[0]
    # still stored -- partial evidence beats no evidence, just loudly flagged
    assert storage.exists("sources", "sbi_card/reward_terms-2026-08-31.pdf")


def _bundle_fixture(tmp_path: Path) -> Path:
    bundle = {
        "issuer_key": "zz_test_issuer",
        "key": "zz_test_card",
        "sources": {
            "src_a": {"url": "https://example.com/a.pdf", "source_type": "mitc"},
            "src_b": {"url": "https://example.com/b.pdf", "source_type": "reward_terms"},
        },
    }
    path = tmp_path / "bundle.json"
    path.write_text(json.dumps(bundle))
    return path


def test_capture_bundle_captures_every_declared_source_and_rewrites_the_file(tmp_path):
    path = _bundle_fixture(tmp_path)
    storage = FakeStorageBackend()

    results = capture_bundle(path, storage, fetcher=_ok_fetcher(), today="2026-08-31")
    assert {r.source_key for r in results} == {"src_a", "src_b"}

    updated = json.loads(path.read_text())
    assert updated["sources"]["src_a"]["storage_path"] == "sources/zz_test_issuer/src_a-2026-08-31.pdf"
    assert updated["sources"]["src_a"]["captured_at"] == "2026-08-31"
    assert updated["sources"]["src_b"]["storage_path"] == "sources/zz_test_issuer/src_b-2026-08-31.pdf"


def test_capture_bundle_is_idempotent_by_default(tmp_path):
    path = _bundle_fixture(tmp_path)
    storage = FakeStorageBackend()
    capture_bundle(path, storage, fetcher=_ok_fetcher(), today="2026-08-31")

    # second run: nothing left to do, no new fetches/uploads
    calls = []
    def counting_fetcher(url):
        calls.append(url)
        return FetchResult(ok=True, content=b"%PDF-1.4\nx", content_type="application/pdf", error=None)

    results = capture_bundle(path, storage, fetcher=counting_fetcher, today="2026-09-01")
    assert results == []
    assert calls == []


def test_capture_bundle_force_recaptures_and_updates_captured_at(tmp_path):
    path = _bundle_fixture(tmp_path)
    storage = FakeStorageBackend()
    capture_bundle(path, storage, fetcher=_ok_fetcher(), today="2026-08-31")

    results = capture_bundle(path, storage, fetcher=_ok_fetcher(), force=True, today="2026-09-15")
    assert {r.source_key for r in results} == {"src_a", "src_b"}
    updated = json.loads(path.read_text())
    assert updated["sources"]["src_a"]["captured_at"] == "2026-09-15"


def test_capture_bundle_restricts_to_named_source_keys(tmp_path):
    path = _bundle_fixture(tmp_path)
    storage = FakeStorageBackend()

    results = capture_bundle(path, storage, source_keys=["src_a"], fetcher=_ok_fetcher(), today="2026-08-31")
    assert {r.source_key for r in results} == {"src_a"}
    updated = json.loads(path.read_text())
    assert "storage_path" not in updated["sources"]["src_b"]


def test_capture_bundle_supports_manual_file_per_source_key(tmp_path):
    path = _bundle_fixture(tmp_path)
    manual = tmp_path / "manual_a.pdf"
    manual.write_bytes(_minimal_valid_pdf_bytes(1))
    storage = FakeStorageBackend()

    results = capture_bundle(
        path, storage, source_keys=["src_a"], manual_files={"src_a": manual},
        fetcher=_failing_fetcher(), today="2026-08-31",
    )
    assert results[0].method == "manual"


def test_capture_bundle_raises_on_unknown_source_key(tmp_path):
    path = _bundle_fixture(tmp_path)
    storage = FakeStorageBackend()
    with pytest.raises(CaptureError, match="not declared"):
        capture_bundle(path, storage, source_keys=["not_a_real_key"], fetcher=_ok_fetcher())


def test_capture_bundle_raises_when_bundle_has_no_sources(tmp_path):
    path = tmp_path / "empty.json"
    path.write_text(json.dumps({"issuer_key": "zz", "key": "zz_card"}))
    storage = FakeStorageBackend()
    with pytest.raises(CaptureError, match="no sources"):
        capture_bundle(path, storage, fetcher=_ok_fetcher())
