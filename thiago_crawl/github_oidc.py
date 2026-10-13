from __future__ import annotations

import json
import os
from collections.abc import Mapping
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from urllib.request import Request, urlopen


def get_actions_oidc_token(
    audience: str,
    env: Mapping[str, str] = os.environ,
) -> str:
    if not isinstance(audience, str) or not audience.strip() or len(audience) > 256:
        raise ValueError("audience must be a non-empty string up to 256 characters")

    request_url = env.get("ACTIONS_ID_TOKEN_REQUEST_URL", "")
    request_token = env.get("ACTIONS_ID_TOKEN_REQUEST_TOKEN", "")
    if not request_url or not request_token:
        raise RuntimeError("GitHub ACTIONS_ID_TOKEN request environment is unavailable")

    parsed = urlsplit(request_url)
    if parsed.scheme != "https" or not parsed.netloc:
        raise RuntimeError("GitHub OIDC request URL is invalid")
    query = [(key, value) for key, value in parse_qsl(parsed.query, keep_blank_values=True) if key != "audience"]
    query.append(("audience", audience))
    target = urlunsplit((parsed.scheme, parsed.netloc, parsed.path, urlencode(query), parsed.fragment))

    request = Request(
        target,
        headers={
            "Authorization": f"Bearer {request_token}",
            "Accept": "application/json",
        },
        method="GET",
    )
    try:
        with urlopen(request, timeout=10) as response:
            status = int(getattr(response, "status", 200) or 200)
            if status < 200 or status >= 300:
                raise RuntimeError(f"GitHub OIDC token request failed with HTTP {status}")
            body = response.read()
    except HTTPError as exc:
        raise RuntimeError(f"GitHub OIDC token request failed with HTTP {exc.code}") from None
    except URLError:
        raise RuntimeError("GitHub OIDC token request failed") from None

    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError, TypeError):
        raise RuntimeError("GitHub OIDC token response is invalid") from None
    token = payload.get("value") if isinstance(payload, dict) else None
    if not isinstance(token, str) or not token.strip():
        raise RuntimeError("GitHub OIDC token response is invalid")
    return token
