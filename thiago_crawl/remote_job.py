from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlsplit, urlunsplit
from urllib.request import Request, urlopen

from .chunking import chunk_utf8
from .crawler import CrawlRun
from .models import CrawlRequest, NormalizedDocument
from .security import registrable_domain

MAX_TEXT_CHUNK_BYTES = 512 * 1024
DEFAULT_MAX_BODY_BYTES = 786_432
CONTROL_PLANE_USER_AGENT = "Mozilla/5.0 (compatible; ThiagoCrawlWorker/1.0; +https://github.com/thiagosaruman/thiago-crawl-worker)"
_JOB_ID = re.compile(r"[A-Za-z0-9_-]{8,128}\Z")
_SAFE_REMOTE_ERROR = re.compile(r"[a-z][a-z0-9_]{0,63}\Z")
_MAX_ERROR_BODY_BYTES = 4096


class RemoteJobError(RuntimeError):
    def __init__(self, category: str, *, status: int | None = None, remote_error: str | None = None):
        self.category = category
        self.status = status
        self.remote_error = remote_error
        suffix = f" ({remote_error})" if remote_error is not None else ""
        super().__init__(f"remote job request failed: {category}{suffix}")


def _json_bytes(value: Any) -> bytes:
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    except (TypeError, ValueError, UnicodeError):
        raise RemoteJobError("invalid_payload") from None


def _base_url(value: str) -> str:
    try:
        parsed = urlsplit(value)
    except ValueError:
        raise RemoteJobError("invalid_control_plane_url") from None
    if parsed.scheme != "https" or not parsed.hostname or parsed.username is not None or parsed.password is not None:
        raise RemoteJobError("invalid_control_plane_url")
    path = parsed.path.rstrip("/")
    return urlunsplit(("https", parsed.netloc, path, "", ""))


def _job_id(value: str) -> str:
    if not isinstance(value, str) or not _JOB_ID.fullmatch(value):
        raise RemoteJobError("invalid_job_id")
    return value


def _endpoint(control_plane_url: str, job_id: str, suffix: str) -> str:
    base = _base_url(control_plane_url)
    opaque = quote(_job_id(job_id), safe="_-ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789")
    return f"{base}/internal/jobs/{opaque}/{suffix}"


def _category_for_status(status: int) -> str:
    if status == 401:
        return "unauthorized"
    if status == 403:
        return "forbidden"
    if status == 404:
        return "not_found"
    if status == 409:
        return "conflict"
    if status == 413:
        return "payload_too_large"
    if status == 429:
        return "rate_limited"
    if 500 <= status <= 599:
        return "server_error"
    return "http_error"


def _safe_http_error_code(exc: HTTPError) -> str | None:
    try:
        raw = exc.read(_MAX_ERROR_BODY_BYTES + 1)
    except Exception:
        return None
    if not raw or len(raw) > _MAX_ERROR_BODY_BYTES:
        return None
    try:
        decoded = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError, TypeError):
        return None
    if not isinstance(decoded, dict):
        return None
    value = decoded.get("error")
    if not isinstance(value, str) or not _SAFE_REMOTE_ERROR.fullmatch(value):
        return None
    return value


def _request_json(
    method: str,
    url: str,
    oidc_token: str,
    payload: Any | None = None,
    *,
    timeout: int = 20,
) -> dict[str, Any]:
    if not isinstance(oidc_token, str) or not oidc_token:
        raise RemoteJobError("missing_oidc_token")
    body = None if payload is None else _json_bytes(payload)
    headers = {
        "Authorization": f"Bearer {oidc_token}",
        "Accept": "application/json",
        "User-Agent": CONTROL_PLANE_USER_AGENT,
    }
    if body is not None:
        headers["Content-Type"] = "application/json"
    request = Request(url, data=body, headers=headers, method=method)
    try:
        with urlopen(request, timeout=timeout) as response:
            status = int(getattr(response, "status", 200) or 200)
            if not 200 <= status < 300:
                raise RemoteJobError(_category_for_status(status), status=status)
            raw = response.read()
    except HTTPError as exc:
        raise RemoteJobError(
            _category_for_status(exc.code),
            status=exc.code,
            remote_error=_safe_http_error_code(exc),
        ) from None
    except (URLError, TimeoutError, OSError):
        raise RemoteJobError("network_error") from None

    if not raw:
        return {}
    try:
        decoded = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError, TypeError):
        raise RemoteJobError("invalid_response") from None
    if not isinstance(decoded, dict):
        raise RemoteJobError("invalid_response")
    return decoded


def fetch_job_payload(control_plane_url: str, job_id: str, oidc_token: str) -> CrawlRequest:
    payload = _request_json("GET", _endpoint(control_plane_url, job_id, "payload"), oidc_token)
    if payload.get("schema_version") != 1 or payload.get("job_id") != job_id:
        raise RemoteJobError("invalid_job_payload")
    request = payload.get("request")
    if not isinstance(request, dict):
        raise RemoteJobError("invalid_job_payload")

    targets = request.get("targets")
    topic = request.get("topic")
    mode = request.get("mode")
    max_pages = request.get("max_pages")
    max_depth = request.get("max_depth")
    force_refresh = request.get("force_refresh", False)

    if (
        not isinstance(targets, list)
        or not 1 <= len(targets) <= 20
        or any(not isinstance(target, str) or not target for target in targets)
        or (topic is not None and not isinstance(topic, str))
        or mode not in {"url", "domain", "cross_source"}
        or isinstance(max_pages, bool)
        or not isinstance(max_pages, int)
        or not 1 <= max_pages <= 50
        or isinstance(max_depth, bool)
        or not isinstance(max_depth, int)
        or not 0 <= max_depth <= 2
        or not isinstance(force_refresh, bool)
    ):
        raise RemoteJobError("invalid_job_payload")

    return CrawlRequest(
        targets=tuple(targets),
        topic=topic,
        mode=mode,
        max_pages=max_pages,
        max_depth=max_depth,
        force_refresh=force_refresh,
    )


def _retrieved_at(value: str | None) -> str:
    if value is not None:
        if not isinstance(value, str) or not value:
            raise ValueError("retrieved_at must be a non-empty string")
        return value
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _source_name(document: NormalizedDocument) -> str | None:
    value = document.metadata.get("site_name")
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _document_envelope(document: NormalizedDocument, retrieved_at: str) -> dict[str, Any]:
    return {
        "canonicalUrl": document.canonical_url,
        "registrableDomain": registrable_domain(document.canonical_url),
        "sourceName": _source_name(document),
        "documentType": document.document_type,
        "language": document.language,
        "retrievedAt": retrieved_at,
        "title": document.title,
        "authors": list(document.authors),
        "publishedAt": document.published_at,
        "contentHash": document.content_hash,
        "httpStatus": document.http_status,
        "fetchStrategy": document.fetch_strategy.value,
        "extractor": document.extractor,
        "metadata": document.metadata,
        "textByteLength": len(document.text_content.encode("utf-8")),
        "links": [
            {"targetUrl": link, "anchorText": None, "relationshipType": "link"}
            for link in document.links
        ],
    }


def _add_part_key(part_without_key: dict[str, Any]) -> dict[str, Any]:
    digest = hashlib.sha256(_json_bytes(part_without_key)).hexdigest()
    return {"schemaVersion": part_without_key["schemaVersion"], "partKey": f"part_{digest}", "documents": part_without_key["documents"], "failures": part_without_key["failures"]}


def _document_parts(
    document: NormalizedDocument,
    retrieved_at: str,
    max_body_bytes: int,
) -> list[dict[str, Any]]:
    envelope = _document_envelope(document, retrieved_at)
    chunk_limit = min(MAX_TEXT_CHUNK_BYTES, max(1024, max_body_bytes - 131_072))

    while chunk_limit >= 1024:
        chunks = chunk_utf8(document.text_content, chunk_limit)
        if not chunks:
            chunks = ("",)
        total_chunks = len(chunks)
        parts: list[dict[str, Any]] = []
        fits = True
        for index, text in enumerate(chunks):
            fragment = {
                **envelope,
                "totalChunks": total_chunks,
                "chunkStart": index,
                "textChunks": [{"index": index, "text": text}],
            }
            part = _add_part_key({"schemaVersion": 2, "documents": [fragment], "failures": []})
            if len(_json_bytes(part)) > max_body_bytes:
                fits = False
                break
            parts.append(part)
        if fits:
            return parts
        chunk_limit //= 2

    raise RemoteJobError("result_fragment_too_large")


def _failure_part(failure: Any, max_body_bytes: int) -> dict[str, Any]:
    item = {
        "url": str(getattr(failure, "url", "")) or None,
        "category": str(getattr(failure, "category", "failure"))[:64] or "failure",
        "message": str(getattr(failure, "message", ""))[:1000] or None,
    }
    part = _add_part_key({"schemaVersion": 2, "documents": [], "failures": [item]})
    if len(_json_bytes(part)) > max_body_bytes:
        raise RemoteJobError("result_fragment_too_large")
    return part


def build_result_parts(
    run: CrawlRun,
    max_body_bytes: int = DEFAULT_MAX_BODY_BYTES,
    *,
    retrieved_at: str | None = None,
) -> tuple[dict[str, Any], ...]:
    if not isinstance(max_body_bytes, int) or isinstance(max_body_bytes, bool) or max_body_bytes < 4096:
        raise ValueError("max_body_bytes must be an integer of at least 4096")
    timestamp = _retrieved_at(retrieved_at)
    parts: list[dict[str, Any]] = []
    for document in run.documents:
        parts.extend(_document_parts(document, timestamp, max_body_bytes))
    for failure in run.failures:
        parts.append(_failure_part(failure, max_body_bytes))
    if len(parts) > 200:
        raise RemoteJobError("too_many_result_parts")
    return tuple(parts)


def post_result_part(
    control_plane_url: str,
    job_id: str,
    part: dict[str, Any],
    oidc_token: str,
) -> dict[str, Any]:
    body = _json_bytes(part)
    if len(body) > DEFAULT_MAX_BODY_BYTES:
        raise RemoteJobError("payload_too_large", status=413)
    return _request_json("POST", _endpoint(control_plane_url, job_id, "parts"), oidc_token, part)


def finalize_job(
    control_plane_url: str,
    job_id: str,
    summary: dict[str, Any],
    oidc_token: str,
) -> dict[str, Any]:
    return _request_json("POST", _endpoint(control_plane_url, job_id, "finalize"), oidc_token, summary)
