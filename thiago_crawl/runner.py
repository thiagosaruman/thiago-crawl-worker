from __future__ import annotations

import argparse
import asyncio
import time
from collections.abc import Callable
from typing import Any

from .crawler import CrawlRun, crawl
from .fetcher import ScraplingFetcher
from .github_oidc import get_actions_oidc_token
from .remote_job import build_result_parts, fetch_job_payload, finalize_job, post_result_part

CONTROL_PLANE_URL = "https://thiago-crawl-build.thiagosaruman.workers.dev"
OIDC_AUDIENCE = "thiago-crawl-control-plane"


def _safe_error_summary(run: CrawlRun) -> str | None:
    categories = sorted({str(failure.category)[:64] for failure in run.failures if failure.category})
    if not categories:
        return None
    return ",".join(categories)[:1000]


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


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    result = asyncio.run(run_remote_job(args.job_id))
    status = str(result.get("status", "unknown"))
    print(f"remote_job_status={status}")
    return 0 if status in {"completed", "partial"} else 2


if __name__ == "__main__":
    raise SystemExit(main())
