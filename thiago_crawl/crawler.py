from __future__ import annotations

import re
from collections import deque
from dataclasses import dataclass
from typing import Protocol
from urllib.parse import urljoin

from .extractors import extract_document
from .models import CrawlRequest, NormalizedDocument
from .security import normalize_url, registrable_domain, validate_limits


class AsyncFetcher(Protocol):
    async def fetch(self, url: str, topic: str | None = None): ...


@dataclass(frozen=True)
class CrawlFailure:
    url: str
    category: str
    message: str
    depth: int


@dataclass(frozen=True)
class CrawlRun:
    request: CrawlRequest
    status: str
    attempted: int
    documents: tuple[NormalizedDocument, ...]
    failures: tuple[CrawlFailure, ...]


def _topic_tokens(topic: str | None) -> tuple[str, ...]:
    if not topic:
        return ()
    return tuple(token for token in re.findall(r"\w+", topic.casefold(), flags=re.UNICODE) if len(token) >= 2)


def _link_score(url: str, tokens: tuple[str, ...]) -> int:
    lowered = url.casefold()
    return sum(1 for token in tokens if token in lowered)


def _normalized_link(base_url: str, raw: str) -> str | None:
    absolute = urljoin(base_url, raw)
    normalized = normalize_url(absolute)
    if not normalized.startswith(("http://", "https://")):
        return None
    return normalized


def _failure_from_exception(url: str, depth: int, exc: Exception, *, default: str) -> CrawlFailure:
    category = str(getattr(exc, "category", default) or default)
    return CrawlFailure(url=url, category=category, message=str(exc), depth=depth)


async def crawl(request: CrawlRequest, fetcher: AsyncFetcher) -> CrawlRun:
    max_pages, max_depth = validate_limits(request.max_pages, request.max_depth)
    if request.mode not in {"url", "domain", "cross_source"}:
        raise ValueError("mode must be 'url', 'domain', or 'cross_source'")
    if not request.targets:
        raise ValueError("at least one target is required")

    seeds = tuple(normalize_url(target) for target in request.targets)
    allowed_domains = {registrable_domain(seed) for seed in seeds}
    queue: deque[tuple[str, int]] = deque((seed, 0) for seed in seeds)
    seen = set(seeds)
    tokens = _topic_tokens(request.topic)
    documents: list[NormalizedDocument] = []
    canonical_seen: set[str] = set()
    failures: list[CrawlFailure] = []
    attempted = 0

    while queue and attempted < max_pages:
        url, depth = queue.popleft()
        attempted += 1
        try:
            page = await fetcher.fetch(url, request.topic)
        except Exception as exc:
            failures.append(_failure_from_exception(url, depth, exc, default="fetch_error"))
            continue

        try:
            document = extract_document(page)
        except Exception as exc:
            failures.append(_failure_from_exception(url, depth, exc, default="extraction_failure"))
            continue

        canonical = normalize_url(document.canonical_url)
        if canonical not in canonical_seen:
            canonical_seen.add(canonical)
            documents.append(document)

        if request.mode == "url" or depth >= max_depth:
            continue

        candidates: list[tuple[int, int, str]] = []
        for index, raw_link in enumerate(document.links):
            try:
                link = _normalized_link(page.url, raw_link)
                if link is None or link in seen:
                    continue
                if registrable_domain(link) not in allowed_domains:
                    continue
            except ValueError:
                continue
            seen.add(link)
            candidates.append((-_link_score(link, tokens), index, link))

        candidates.sort()
        for _, _, link in candidates:
            queue.append((link, depth + 1))

    if documents and failures:
        status = "partial"
    elif documents:
        status = "completed"
    else:
        status = "failed"

    return CrawlRun(
        request=request,
        status=status,
        attempted=attempted,
        documents=tuple(documents),
        failures=tuple(failures),
    )
