from __future__ import annotations

import json
import re
from typing import Any

_SENSITIVE_KEYS = {
    "authorization", "body", "content", "cookie", "cookies", "headers", "html", "payload", "target", "targets", "text", "topic", "url", "urls",
}
_URL_RE = re.compile(r"https?://[^\s'\"<>]+", re.IGNORECASE)
_AUTH_RE = re.compile(r"\bAuthorization\s*:\s*(?:Bearer|Basic)\s+[^\s]+", re.IGNORECASE)
_BEARER_RE = re.compile(r"\bBearer\s+[A-Za-z0-9._~+/=-]+", re.IGNORECASE)
_COOKIE_RE = re.compile(r"\b(Cookie|Set-Cookie)\s*:\s*.*?(?=\s+(?:category|count|attempted|succeeded|failed|strategy|status|terminal)=|$)", re.IGNORECASE)


def _scrub_json(value: Any, *, key: str | None = None) -> Any:
    if key is not None and key.lower() in _SENSITIVE_KEYS:
        return "[redacted]"
    if isinstance(value, dict):
        return {str(k): _scrub_json(v, key=str(k)) for k, v in value.items()}
    if isinstance(value, list):
        return [_scrub_json(item) for item in value]
    return value


def redact_log_message(message: str) -> str:
    if not isinstance(message, str):
        message = str(message)
    text = message
    stripped = message.strip()
    if stripped.startswith(("{", "[")):
        try:
            parsed = json.loads(message)
        except (json.JSONDecodeError, TypeError):
            pass
        else:
            text = json.dumps(_scrub_json(parsed), ensure_ascii=False, separators=(",", ":"))
    text = _AUTH_RE.sub("Authorization: [redacted]", text)
    text = _BEARER_RE.sub("Bearer [redacted]", text)
    text = _COOKIE_RE.sub(lambda match: f"{match.group(1)}: [redacted]", text)
    text = _URL_RE.sub("[redacted-url]", text)
    return text
