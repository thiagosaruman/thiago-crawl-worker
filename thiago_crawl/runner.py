from __future__ import annotations

import argparse
import asyncio
import json
import re
import time
from collections.abc import Callable
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .crawler import CrawlRun, crawl
from .fetcher import ScraplingFetcher
from .github_oidc import get_actions_oidc_token
from .remote_job import (
    CONTROL_PLANE_USER_AGENT,
    RemoteJobError,
    build_result_parts,
    fetch_job_payload,
    finalize_job,
    post_result_part,
)

CONTROL_PLANE_URL = "https://thiago-crawl-build.thiagosaruman.workers.dev"
OIDC_AUDIENCE = "thiago-crawl-control-plane"
_SAFE_REMOTE_ERROR = re.compile(r"[a-z][a-z0-9_]{0,63}\Z")
_MAX_PROBE_BODY_BYTES = 4096


def _safe_error_summary(run: CrawlRun) -> str | None:
    categories = sorted({str(failure.category)[:64] for failure in run.failures if failure.category})
    if not categories:
        return None
    return ",".join(categories)[:1000]


def _safe_probe_error(raw: bytes) -> str | None:
    if not raw or len(raw) > _MAX_PROBE_BODY_BYTES:
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


def _probe_url(url: str) -> dict[str, int | str | None]:
    request = Request(
        url,
        headers={"Accept": "application/json", "User-Agent": CONTROL_PLANE_USER_AGENT},
        method="GET",
    )
    try:
        with urlopen(request, timeout=20) as response:
            status = int(getattr(response, "status", 200) or 200)
            raw = response.read(_MAX_PROBE_BODY_BYTES + 1)
            return {"status": status, "error": _safe_probe_error(raw)}
    except HTTPError as exc:
        try:
            raw = exc.read(_MAX_PROBE_BODY_BYTES + 1)
        except Exception:
            raw = b""
        return {"status": int(exc.code), "error": _safe_probe_error(raw)}
    except (URLError, TimeoutError, OSError):
        return {"status": None, "error": "network_error"}


def probe_control_plane_boundary(control_plane_url: str, job_id: str) -> dict[str, dict[str, int | str | None]]:
    base = control_plane_url.rstrip("/")
    opaque_job_id = re.fullmatch(r"[A-Za-z0-9_-]{8,128}", job_id)
    if opaque_job_id is None:
        raise ValueError("invalid opaque job_id")
    return {
        "health": _probe_url(f"{base}/health"),
        "internal_unauth": _probe_url(f"{base}/internal/jobs/{job_id}/payload"),
    }


async def run_remote_job(
    job_id: str,
    *,
    fetcher: Any | None = None,
    token_provider: Callable[[str], str] = get_actions_oidc_token,
    fetch_payload: Callable[[str, str, str], Any] = fetch_job_payload,
    crawl_fn: Callable[..., Any] = crawl,
    build_parts: Callable[[CrawlRun], tuple[dict[str, Any], ...]] = build_result_parts,
    post_part: Callable[[str, str, dict[str, Any], str], dict[str, Any]] = post_result_part,
    finalize: Callable[[str, str, dict[str, Any], str], dict[str, Any]] = finalize_job,
) -> dict[str, Any]:
    started = time.monotonic()

    payload_token = token_provider(OIDC_AUDIENCE)
    request = fetch_payload(CONTROL_PLANE_URL, job_id, payload_token)

    actual_fetcher = fetcher if fetcher is not None else ScraplingFetcher()
    run = await crawl_fn(request, actual_fetcher)
    parts = build_parts(run)

    result_token = token_provider(OIDC_AUDIENCE)
    for part in parts:
        post_part(CONTROL_PLANE_URL, job_id, part, result_token)

    failed = len(run.failures)
    succeeded = max(0, run.attempted - failed)
    summary: dict[str, Any] = {
        "schemaVersion": 1,
        "totalParts": len(parts),
        "pagesAttempted": run.attempted,
        "pagesSucceeded": succeeded,
        "pagesFailed": failed,
        "actualRuntimeMinutes": max(0.0, (time.monotonic() - started) / 60.0),
    }
    error_summary = _safe_error_summary(run)
    if error_summary is not None:
        summary["errorSummary"] = error_summary

    return finalize(CONTROL_PLANE_URL, job_id, summary, result_token)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run one opaque Thiago Crawl control-plane job")
    parser.add_argument("--job-id", required=True)
    return parser


def _format_probe_value(value: int | str | None) -> str:
    return "none" if value is None else str(value)


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        boundary = probe_control_plane_boundary(CONTROL_PLANE_URL, args.job_id)
        print(
            "boundary_health_status=" + _format_probe_value(boundary["health"]["status"])
            + " boundary_health_error=" + _format_probe_value(boundary["health"]["error"])
        )
        print(
            "boundary_internal_unauth_status=" + _format_probe_value(boundary["internal_unauth"]["status"])
            + " boundary_internal_unauth_error=" + _format_probe_value(boundary["internal_unauth"]["error"])
        )
        result = asyncio.run(run_remote_job(args.job_id))
    except RemoteJobError as exc:
        line = f"remote_job_error={exc.category}"
        if exc.remote_error is not None:
            line += f" remote_job_code={exc.remote_error}"
        print(line)
        return 2
    except Exception:
        print("remote_job_error=unexpected")
        return 2

    status = str(result.get("status", "unknown"))
    print(f"remote_job_status={status}")
    return 0 if status in {"completed", "partial"} else 2


if __name__ == "__main__":
    raise SystemExit(main())
