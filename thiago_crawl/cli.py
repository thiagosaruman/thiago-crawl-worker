from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

from .artifacts import write_artifacts
from .crawler import crawl
from .fetcher import ScraplingFetcher
from .models import CrawlRequest
from .security import validate_limits


def _pages(value: str) -> int:
    parsed = int(value)
    validate_limits(parsed, 0)
    return parsed


def _depth(value: str) -> int:
    parsed = int(value)
    validate_limits(1, parsed)
    return parsed


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Bounded Thiago Crawl worker")
    parser.add_argument("url")
    parser.add_argument("--mode", choices=("url", "domain"), default="url")
    parser.add_argument("--topic", default=None)
    parser.add_argument("--max-pages", type=_pages, default=10)
    parser.add_argument("--max-depth", type=_depth, default=1)
    parser.add_argument("--output", default="output")
    return parser


async def _run(args: argparse.Namespace) -> int:
    request = CrawlRequest(
        targets=(args.url,),
        topic=args.topic,
        mode=args.mode,
        max_pages=args.max_pages,
        max_depth=args.max_depth,
    )
    result = await crawl(request, ScraplingFetcher())
    write_artifacts(result, Path(args.output))
    return 2 if result.status == "failed" else 0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return asyncio.run(_run(args))


if __name__ == "__main__":
    raise SystemExit(main())
