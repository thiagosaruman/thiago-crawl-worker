import asyncio
import io
import json
from types import SimpleNamespace
from urllib.error import HTTPError

from thiago_crawl.crawler import CrawlFailure, CrawlRun
from thiago_crawl.models import CrawlRequest
from thiago_crawl.runner import (
    CONTROL_PLANE_URL,
    OIDC_AUDIENCE,
    probe_control_plane_boundary,
    run_remote_job,
)


class FakeResponse:
    def __init__(self, body: bytes = b"{}", status: int = 200):
        self._body = io.BytesIO(body)
        self.status = status

    def read(self, amount: int = -1):
        return self._body.read(amount)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


def test_remote_runner_orchestrates_private_payload_crawl_parts_and_finalize_without_logging_content():
    request = CrawlRequest(
        targets=("https://example.com/private-target",),
        topic="private topic",
        mode="url",
        max_pages=1,
        max_depth=0,
        force_refresh=True,
    )
    run = CrawlRun(
        request=request,
        status="partial",
        attempted=2,
        documents=(SimpleNamespace(),),
        failures=(CrawlFailure(url="https://example.com/fail", category="blocked", message="blocked", depth=0),),
    )
    events = []
    tokens = iter(["oidc-payload", "oidc-results"])

    def token_provider(audience):
        events.append(("token", audience))
        return next(tokens)

    def fetch_payload(base_url, job_id, token):
        events.append(("payload", base_url, job_id, token))
        return request

    async def crawl_fn(received_request, fetcher):
        events.append(("crawl", received_request, fetcher))
        return run

    parts = ({"schemaVersion": 2, "partKey": "p1", "documents": [], "failures": []},)

    def build_parts(received_run):
        events.append(("build", received_run))
        return parts

    def post_part(base_url, job_id, part, token):
        events.append(("post", base_url, job_id, part["partKey"], token))
        return {"replayed": False}

    finalized = {}

    def finalize(base_url, job_id, summary, token):
        finalized.update(summary)
        events.append(("finalize", base_url, job_id, token))
        return {"status": "partial"}

    result = asyncio.run(run_remote_job(
        "job_12345678",
        fetcher=object(),
        token_provider=token_provider,
        fetch_payload=fetch_payload,
        crawl_fn=crawl_fn,
        build_parts=build_parts,
        post_part=post_part,
        finalize=finalize,
    ))

    assert result == {"status": "partial"}
    assert events[0] == ("token", OIDC_AUDIENCE)
    assert events[1] == ("payload", CONTROL_PLANE_URL, "job_12345678", "oidc-payload")
    assert ("token", OIDC_AUDIENCE) in events[3:]
    assert ("post", CONTROL_PLANE_URL, "job_12345678", "p1", "oidc-results") in events
    assert finalized["schemaVersion"] == 1
    assert finalized["totalParts"] == 1
    assert finalized["pagesAttempted"] == 2
    assert finalized["pagesSucceeded"] == 1
    assert finalized["pagesFailed"] == 1
    assert isinstance(finalized["actualRuntimeMinutes"], (int, float))
    assert finalized["actualRuntimeMinutes"] >= 0


def test_remote_runner_uses_fixed_https_control_plane_and_non_url_oidc_audience():
    assert CONTROL_PLANE_URL == "https://thiago-crawl-build.thiagosaruman.workers.dev"
    assert OIDC_AUDIENCE == "thiago-crawl-control-plane"
    assert OIDC_AUDIENCE not in CONTROL_PLANE_URL


def test_boundary_probe_reports_only_safe_status_and_error_code(monkeypatch):
    seen = []

    def fake_urlopen(request, timeout):
        seen.append((request.full_url, request.get_header("Authorization"), timeout))
        if request.full_url.endswith("/health"):
            return FakeResponse(b'{"service":"thiago-crawl","secret":"do-not-leak"}', 200)
        body = json.dumps({
            "error": "runner_auth_required",
            "secret": "do-not-leak",
            "url": "https://secret.example/internal",
        }).encode()
        raise HTTPError(request.full_url, 401, "Unauthorized", {}, io.BytesIO(body))

    monkeypatch.setattr("thiago_crawl.runner.urlopen", fake_urlopen)
    result = probe_control_plane_boundary(CONTROL_PLANE_URL, "job_12345678")

    assert result == {
        "health": {"status": 200, "error": None},
        "internal_unauth": {"status": 401, "error": "runner_auth_required"},
    }
    assert all(authorization is None for _, authorization, _ in seen)
    assert all(timeout == 20 for _, _, timeout in seen)
    assert "do-not-leak" not in repr(result)
    assert "secret.example" not in repr(result)
