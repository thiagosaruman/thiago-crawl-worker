import json
from pathlib import Path

from thiago_crawl.artifacts import write_artifacts
from thiago_crawl.crawler import CrawlFailure, CrawlRun
from thiago_crawl.models import CrawlRequest, FetchStrategy, NormalizedDocument


def make_document(url, *, title="Título café", extractor="generic", strategy=FetchStrategy.STATIC, metadata=None):
    return NormalizedDocument(canonical_url=url, document_type="generic", title=title, authors=("Autora",), published_at="2026-10-10", language="pt-BR", text_content="Olá mundo com café e conteúdo útil.", content_hash="a" * 64, links=(url + "/linked",), fetch_strategy=strategy, extractor=extractor, metadata=metadata or {})


def make_run(*, failures=()):
    request = CrawlRequest(("https://example.com/",), "café", "domain", 10, 2)
    return CrawlRun(request=request, status="partial" if failures else "completed", attempted=2 if failures else 1, documents=(make_document("https://example.com/"),), failures=tuple(failures))


def test_write_artifacts_emits_v2_manifest_jsonl_index_and_markdown(tmp_path: Path):
    manifest = write_artifacts(make_run(), tmp_path)
    assert manifest["schema_version"] == 2
    assert manifest["status"] == "completed"
    assert manifest["attempted_count"] == 1
    assert manifest["succeeded_count"] == 1
    assert manifest["failed_count"] == 0
    assert manifest["strategy_counts"] == {"static": 1}
    assert manifest["extractor_counts"] == {"generic": 1}
    assert (tmp_path / "manifest.json").exists()
    assert (tmp_path / "documents.jsonl").exists()
    assert (tmp_path / "index.md").exists()
    assert (tmp_path / "pages" / "0001.md").exists()


def test_jsonl_has_one_row_per_document_and_preserves_utf8(tmp_path: Path):
    write_artifacts(make_run(), tmp_path)
    rows = (tmp_path / "documents.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(rows) == 1
    payload = json.loads(rows[0])
    assert payload["title"] == "Título café"
    assert payload["text_content"].startswith("Olá mundo")
    assert payload["fetch_strategy"] == "static"


def test_sensitive_metadata_is_removed_recursively_from_artifacts(tmp_path: Path):
    document = make_document("https://example.com/", metadata={"content_type": "text/html", "Authorization": "Bearer secret-token", "cookie": "session=abc", "nested": {"set-cookie": "x=y", "safe": "ok", "api_key": "nope"}})
    run = CrawlRun(CrawlRequest((document.canonical_url,), None, "url", 1, 0), "completed", 1, (document,), ())
    write_artifacts(run, tmp_path)
    text = (tmp_path / "documents.jsonl").read_text(encoding="utf-8")
    assert "Bearer secret-token" not in text
    assert "session=abc" not in text
    assert "x=y" not in text
    assert "nope" not in text
    payload = json.loads(text)
    assert payload["metadata"]["content_type"] == "text/html"
    assert payload["metadata"]["nested"] == {"safe": "ok"}


def test_page_markdown_is_deterministic_and_contains_provenance_not_raw_html(tmp_path: Path):
    run = make_run()
    write_artifacts(run, tmp_path)
    first = (tmp_path / "pages" / "0001.md").read_text(encoding="utf-8")
    write_artifacts(run, tmp_path)
    second = (tmp_path / "pages" / "0001.md").read_text(encoding="utf-8")
    assert first == second
    assert "# Título café" in first
    assert "https://example.com/" in first
    assert "Olá mundo com café" in first
    assert "<html" not in first


def test_partial_manifest_reports_representative_failure(tmp_path: Path):
    failure = CrawlFailure("https://example.com/bad", "blocked", "blocked by origin", 1)
    manifest = write_artifacts(make_run(failures=(failure,)), tmp_path)
    assert manifest["status"] == "partial"
    assert manifest["failed_count"] == 1
    assert manifest["failures"][0]["category"] == "blocked"
    persisted = json.loads((tmp_path / "manifest.json").read_text(encoding="utf-8"))
    assert persisted == manifest
