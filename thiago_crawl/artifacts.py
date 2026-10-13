from __future__ import annotations

import json
import shutil
from collections import Counter
from pathlib import Path
from typing import Any, Mapping

from .crawler import CrawlRun
from .models import NormalizedDocument

_SENSITIVE_KEY_PARTS = (
    "authorization",
    "cookie",
    "set-cookie",
    "token",
    "password",
    "secret",
    "api_key",
    "apikey",
    "proxy_auth",
)


def _sensitive_key(key: object) -> bool:
    lowered = str(key).casefold().replace("-", "_")
    return any(part.replace("-", "_") in lowered for part in _SENSITIVE_KEY_PARTS)


def _sanitize(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _sanitize(item) for key, item in value.items() if not _sensitive_key(key)}
    if isinstance(value, (list, tuple, set)):
        return [_sanitize(item) for item in value]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def _document_payload(document: NormalizedDocument) -> dict[str, Any]:
    return {
        "canonical_url": document.canonical_url,
        "document_type": document.document_type,
        "title": document.title,
        "authors": list(document.authors),
        "published_at": document.published_at,
        "language": document.language,
        "text_content": document.text_content,
        "content_hash": document.content_hash,
        "links": list(document.links),
        "fetch_strategy": document.fetch_strategy.value,
        "extractor": document.extractor,
        "metadata": _sanitize(document.metadata),
    }


def _markdown(document: NormalizedDocument) -> str:
    title = document.title.strip() or document.canonical_url
    lines = [f"# {title}", "", f"Source: {document.canonical_url}", ""]
    if document.authors:
        lines.extend([f"Authors: {', '.join(document.authors)}", ""])
    if document.published_at:
        lines.extend([f"Published: {document.published_at}", ""])
    lines.extend([document.text_content.rstrip(), ""])
    return "\n".join(lines)


def write_artifacts(run: CrawlRun, output_dir: Path) -> dict[str, object]:
    output_dir.mkdir(parents=True, exist_ok=True)
    pages_dir = output_dir / "pages"
    if pages_dir.exists():
        shutil.rmtree(pages_dir)
    pages_dir.mkdir(parents=True)

    document_rows: list[str] = []
    index_lines = ["# Thiago Crawl index", ""]
    strategy_counts = Counter()
    extractor_counts = Counter()

    for index, document in enumerate(run.documents, start=1):
        filename = f"{index:04d}.md"
        (pages_dir / filename).write_text(_markdown(document), encoding="utf-8")
        document_rows.append(json.dumps(_document_payload(document), ensure_ascii=False, sort_keys=True))
        label = document.title.strip() or document.canonical_url
        index_lines.append(f"- `{filename}` — [{label}]({document.canonical_url})")
        strategy_counts[document.fetch_strategy.value] += 1
        extractor_counts[document.extractor] += 1

    (output_dir / "documents.jsonl").write_text(
        "\n".join(document_rows) + ("\n" if document_rows else ""), encoding="utf-8"
    )
    (output_dir / "index.md").write_text("\n".join(index_lines).rstrip() + "\n", encoding="utf-8")

    failures = [
        {
            "url": failure.url,
            "category": failure.category,
            "message": failure.message[:1000],
            "depth": failure.depth,
        }
        for failure in run.failures[:20]
    ]
    manifest: dict[str, object] = {
        "schema_version": 2,
        "status": run.status,
        "request": {
            "targets": list(run.request.targets),
            "topic": run.request.topic,
            "mode": run.request.mode,
            "max_pages": run.request.max_pages,
            "max_depth": run.request.max_depth,
            "force_refresh": run.request.force_refresh,
        },
        "attempted_count": run.attempted,
        "succeeded_count": len(run.documents),
        "failed_count": len(run.failures),
        "strategy_counts": dict(sorted(strategy_counts.items())),
        "extractor_counts": dict(sorted(extractor_counts.items())),
        "failures": failures,
        "generated_files": [
            "manifest.json",
            "documents.jsonl",
            "index.md",
            *[f"pages/{index:04d}.md" for index in range(1, len(run.documents) + 1)],
        ],
    }
    (output_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return manifest
