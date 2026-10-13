from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class FetchStrategy(str, Enum):
    STATIC = "static"
    DYNAMIC = "dynamic"
    STEALTH = "stealth"


@dataclass(frozen=True)
class CrawlRequest:
    targets: tuple[str, ...]
    topic: str | None
    mode: str
    max_pages: int
    max_depth: int
    force_refresh: bool = False


@dataclass(frozen=True)
class FetchedPage:
    url: str
    title: str
    html: str
    text_hint: str
    links: tuple[str, ...]
    http_status: int
    strategy: FetchStrategy
    metadata: dict[str, object]


@dataclass(frozen=True)
class NormalizedDocument:
    canonical_url: str
    document_type: str
    title: str
    authors: tuple[str, ...]
    published_at: str | None
    language: str | None
    text_content: str
    content_hash: str
    links: tuple[str, ...]
    fetch_strategy: FetchStrategy
    extractor: str
    metadata: dict[str, object]
