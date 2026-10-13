from dataclasses import FrozenInstanceError
import pytest
from thiago_crawl.models import CrawlRequest, FetchStrategy, FetchedPage, NormalizedDocument

def test_fetch_strategy_has_exact_v1_values(): assert [item.value for item in FetchStrategy]==["static","dynamic","stealth"]
def test_crawl_request_is_immutable_and_preserves_targets():
    request=CrawlRequest(targets=("https://example.com/",),topic="research",mode="domain",max_pages=10,max_depth=2); assert request.targets==("https://example.com/",); assert request.force_refresh is False
    with pytest.raises(FrozenInstanceError): request.max_pages=20
def test_page_and_document_models_keep_provenance_fields():
    page=FetchedPage(url="https://example.com/",title="Example",html="<main>Hello</main>",text_hint="Hello",links=("https://example.com/a",),http_status=200,strategy=FetchStrategy.STATIC,metadata={"content_type":"text/html"}); document=NormalizedDocument(canonical_url=page.url,document_type="generic",title=page.title,authors=(),published_at=None,language="en",text_content="Hello",content_hash="abc",links=page.links,fetch_strategy=page.strategy,extractor="generic",metadata=page.metadata); assert document.canonical_url==page.url; assert document.fetch_strategy is FetchStrategy.STATIC
