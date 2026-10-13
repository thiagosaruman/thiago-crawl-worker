import io
from urllib.parse import parse_qs, urlsplit

import pytest

from thiago_crawl.github_oidc import get_actions_oidc_token


class FakeResponse:
    def __init__(self, body: bytes, status: int = 200):
        self._body = io.BytesIO(body)
        self.status = status

    def read(self):
        return self._body.read()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


def test_requires_github_oidc_environment(monkeypatch):
    monkeypatch.delenv("ACTIONS_ID_TOKEN_REQUEST_URL", raising=False)
    monkeypatch.delenv("ACTIONS_ID_TOKEN_REQUEST_TOKEN", raising=False)
    with pytest.raises(RuntimeError, match="ACTIONS_ID_TOKEN"):
        get_actions_oidc_token("thiago-crawl-control-plane")


def test_requests_dedicated_audience_with_runner_bearer_token(monkeypatch):
    seen = {}

    def fake_urlopen(request, timeout):
        seen["url"] = request.full_url
        seen["authorization"] = request.get_header("Authorization")
        seen["accept"] = request.get_header("Accept")
        seen["timeout"] = timeout
        return FakeResponse(b'{"value":"header.payload.signature"}')

    monkeypatch.setenv("ACTIONS_ID_TOKEN_REQUEST_URL", "https://token.actions.example/request?x=1")
    monkeypatch.setenv("ACTIONS_ID_TOKEN_REQUEST_TOKEN", "runner-request-secret")
    monkeypatch.setattr("thiago_crawl.github_oidc.urlopen", fake_urlopen)

    token = get_actions_oidc_token("thiago-crawl-control-plane")

    assert token == "header.payload.signature"
    parsed = urlsplit(seen["url"])
    assert parse_qs(parsed.query) == {"x": ["1"], "audience": ["thiago-crawl-control-plane"]}
    assert seen["authorization"] == "Bearer runner-request-secret"
    assert seen["accept"] == "application/json"
    assert seen["timeout"] == 10


def test_rejects_invalid_oidc_response_without_echoing_runner_secret(monkeypatch):
    def fake_urlopen(request, timeout):
        return FakeResponse(b'{"value":""}')

    monkeypatch.setenv("ACTIONS_ID_TOKEN_REQUEST_URL", "https://token.actions.example/request")
    monkeypatch.setenv("ACTIONS_ID_TOKEN_REQUEST_TOKEN", "do-not-leak")
    monkeypatch.setattr("thiago_crawl.github_oidc.urlopen", fake_urlopen)

    with pytest.raises(RuntimeError) as caught:
        get_actions_oidc_token("thiago-crawl-control-plane")
    assert "do-not-leak" not in str(caught.value)
