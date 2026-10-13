import io
import json
from urllib.error import HTTPError, URLError

import pytest

from thiago_crawl.crawler import CrawlRun
from thiago_crawl.extractors import content_hash
from thiago_crawl.models import CrawlRequest, FetchStrategy, NormalizedDocument
from thiago_crawl.remote_job import (
    RemoteJobError,
    build_result_parts,
    fetch_job_payload,
    finalize_job,
    post_result_part,
)


class FakeResponse:
    def __init__(self, body: bytes = b"{}", status: int = 200):
        self._body = io.BytesIO(body)
        self.status = status

    def read(self):
        return self._body.read()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


def sample_document(text: str = "evidência normalizada") -> NormalizedDocument:
    return NormalizedDocument(
        canonical_url="https://example.com/article",
        document_type="news",
        title="Título",
        authors=("Autora",),
        published_at="2026-10-13T10:00:00-03:00",
        language="pt-BR",
        text_content=text,
        content_hash=content_hash(text),
        links=("https://example.com/source",),
        fetch_strategy=FetchStrategy.STATIC,
        extractor="newspaper4k",
        metadata={"site_name": "Example"},
        http_status=200,
    )


def sample_run(text: str = "evidência normalizada") -> CrawlRun:
    request = CrawlRequest(
        targets=("https://example.com/article",),
        topic="synthetic",
        mode="url",
        max_pages=1,
        max_depth=0,
    )
    return CrawlRun(request=request, status="completed", attempted=1, documents=(sample_document(text),), failures=())


def json_bytes(value) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def test_fetch_job_payload_uses_private_bearer_and_validates_request(monkeypatch):
    seen = {}
    job_id = "job_opaque_123"
    response = {
        "schema_version": 1,
        "job_id": job_id,
        "request": {
            "targets": ["https://example.com/article"],
            "topic": "synthetic",
            "mode": "url",
            "max_pages": 1,
            "max_depth": 0,
            "force_refresh": False,
        },
    }

    def fake_urlopen(request, timeout):
        seen["url"] = request.full_url
        seen["authorization"] = request.get_header("Authorization")
        seen["accept"] = request.get_header("Accept")
        seen["timeout"] = timeout
        return FakeResponse(json.dumps(response).encode())

    monkeypatch.setattr("thiago_crawl.remote_job.urlopen", fake_urlopen)
    request = fetch_job_payload("https://control.example/", job_id, "oidc-secret")

    assert request == CrawlRequest(
        targets=("https://example.com/article",), topic="synthetic", mode="url", max_pages=1, max_depth=0, force_refresh=False
    )
    assert seen == {
        "url": f"https://control.example/internal/jobs/{job_id}/payload",
        "authorization": "Bearer oidc-secret",
        "accept": "application/json",
        "timeout": 20,
    }


def test_fetch_job_payload_rejects_mismatched_job_and_does_not_echo_token_or_body(monkeypatch):
    body = b'{"schema_version":1,"job_id":"job_other","request":{"targets":["https://secret.example/x"],"mode":"url","max_pages":1,"max_depth":0}}'
    monkeypatch.setattr("thiago_crawl.remote_job.urlopen", lambda request, timeout: FakeResponse(body))
    with pytest.raises(RemoteJobError) as caught:
        fetch_job_payload("https://control.example", "job_expected_1", "do-not-leak")
    message = str(caught.value)
    assert "do-not-leak" not in message
    assert "secret.example" not in message


def test_build_result_parts_is_stable_bounded_and_contains_full_provenance():
    parts = build_result_parts(sample_run(), retrieved_at="2026-10-13T13:00:00Z")
    assert parts == build_result_parts(sample_run(), retrieved_at="2026-10-13T13:00:00Z")
    assert len(parts) == 1
    part = parts[0]
    assert part["schemaVersion"] == 2
    assert part["partKey"].startswith("part_")
    assert part["failures"] == []
    document = part["documents"][0]
    assert document["canonicalUrl"] == "https://example.com/article"
    assert document["registrableDomain"] == "example.com"
    assert document["sourceName"] == "Example"
    assert document["contentHash"] == sample_document().content_hash
    assert document["httpStatus"] == 200
    assert document["retrievedAt"] == "2026-10-13T13:00:00Z"
    assert document["textByteLength"] == len(sample_document().text_content.encode("utf-8"))
    assert document["totalChunks"] == 1
    assert document["chunkStart"] == 0
    assert document["textChunks"] == [{"index": 0, "text": sample_document().text_content}]
    assert len(json_bytes(part)) <= 786_432


def test_document_larger_than_two_mib_is_fragmented_and_reconstructs_exactly():
    text = ("ação 🚀 evidência " * 160_000) + "fim"
    assert len(text.encode("utf-8")) > 2 * 1024 * 1024
    run = sample_run(text)
    parts = build_result_parts(run, max_body_bytes=786_432, retrieved_at="2026-10-13T13:00:00Z")

    assert len(parts) > 1
    fragments = [part["documents"][0] for part in parts if part["documents"]]
    assert len(fragments) == len(parts)
    assert all(len(json_bytes(part)) <= 786_432 for part in parts)
    assert all(fragment["contentHash"] == run.documents[0].content_hash for fragment in fragments)
    assert all(fragment["textByteLength"] == len(text.encode("utf-8")) for fragment in fragments)
    assert all(fragment["totalChunks"] == len(fragments) for fragment in fragments)
    assert [fragment["chunkStart"] for fragment in fragments] == list(range(len(fragments)))
    chunks = [fragment["textChunks"][0] for fragment in fragments]
    assert [chunk["index"] for chunk in chunks] == list(range(len(chunks)))
    assert all(len(chunk["text"].encode("utf-8")) <= 512 * 1024 for chunk in chunks)
    assert "".join(chunk["text"] for chunk in chunks) == text


def test_post_part_and_finalize_use_expected_routes_and_map_http_errors(monkeypatch):
    calls = []

    def fake_urlopen(request, timeout):
        calls.append((request.full_url, request.get_method(), request.get_header("Authorization"), request.get_header("Content-type"), request.data, timeout))
        return FakeResponse(b'{"ok":true}')

    monkeypatch.setattr("thiago_crawl.remote_job.urlopen", fake_urlopen)
    part = build_result_parts(sample_run(), retrieved_at="2026-10-13T13:00:00Z")[0]
    assert post_result_part("https://control.example", "job_opaque_123", part, "token-a") == {"ok": True}
    summary = {"schemaVersion": 1, "totalParts": 1, "pagesAttempted": 1, "pagesSucceeded": 1, "pagesFailed": 0, "actualRuntimeMinutes": 0.1}
    assert finalize_job("https://control.example", "job_opaque_123", summary, "token-b") == {"ok": True}
    assert calls[0][0].endswith("/internal/jobs/job_opaque_123/parts")
    assert calls[1][0].endswith("/internal/jobs/job_opaque_123/finalize")
    assert calls[0][1] == "POST"
    assert calls[0][2] == "Bearer token-a"
    assert calls[1][2] == "Bearer token-b"
    assert calls[0][3] == "application/json"

    def unauthorized(request, timeout):
        raise HTTPError(request.full_url, 401, "Unauthorized", {}, None)

    monkeypatch.setattr("thiago_crawl.remote_job.urlopen", unauthorized)
    with pytest.raises(RemoteJobError) as caught:
        post_result_part("https://control.example", "job_opaque_123", part, "secret-token")
    assert caught.value.status == 401
    assert caught.value.category == "unauthorized"
    assert "secret-token" not in str(caught.value)

    def conflict(request, timeout):
        raise HTTPError(request.full_url, 409, "Conflict", {}, None)

    monkeypatch.setattr("thiago_crawl.remote_job.urlopen", conflict)
    with pytest.raises(RemoteJobError) as caught:
        post_result_part("https://control.example", "job_opaque_123", part, "token")
    assert caught.value.status == 409
    assert caught.value.category == "conflict"

    def timeout(request, timeout):
        raise URLError("timed out")

    monkeypatch.setattr("thiago_crawl.remote_job.urlopen", timeout)
    with pytest.raises(RemoteJobError) as caught:
        finalize_job("https://control.example", "job_opaque_123", summary, "token")
    assert caught.value.category == "network_error"


def test_http_error_exposes_only_safe_worker_error_code(monkeypatch):
    body = json.dumps({
        "error": "runner_job_binding_mismatch",
        "token": "do-not-leak",
        "url": "https://secret.example/internal",
    }).encode()

    def forbidden(request, timeout):
        raise HTTPError(request.full_url, 403, "Forbidden", {}, io.BytesIO(body))

    monkeypatch.setattr("thiago_crawl.remote_job.urlopen", forbidden)
    with pytest.raises(RemoteJobError) as caught:
        fetch_job_payload("https://control.example", "job_opaque_123", "oidc-secret")

    assert caught.value.status == 403
    assert caught.value.category == "forbidden"
    assert "runner_job_binding_mismatch" in str(caught.value)
    assert "do-not-leak" not in str(caught.value)
    assert "secret.example" not in str(caught.value)
    assert "oidc-secret" not in str(caught.value)


def test_http_error_ignores_untrusted_error_text(monkeypatch):
    unsafe = "runner_auth_invalid; token=do-not-leak"
    body = json.dumps({"error": unsafe, "secret": "also-do-not-leak"}).encode()

    def forbidden(request, timeout):
        raise HTTPError(request.full_url, 403, "Forbidden", {}, io.BytesIO(body))

    monkeypatch.setattr("thiago_crawl.remote_job.urlopen", forbidden)
    with pytest.raises(RemoteJobError) as caught:
        fetch_job_payload("https://control.example", "job_opaque_123", "oidc-secret")

    message = str(caught.value)
    assert message == "remote job request failed: forbidden"
    assert "do-not-leak" not in message
