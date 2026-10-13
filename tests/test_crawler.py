import asyncio

from thiago_crawl.crawler import crawl
from thiago_crawl.models import CrawlRequest, FetchStrategy, FetchedPage


def run(coro): return asyncio.run(coro)

def make_page(url, *, links=(), canonical=None, title="Page", text=None):
    text = text or ("Useful deterministic page content for crawler testing. " * 6)
    canonical_html = f'<link rel="canonical" href="{canonical}">' if canonical else ""
    links_html = "".join(f'<a href="{link}">{link}</a>' for link in links)
    html = f"<html><head><title>{title}</title>{canonical_html}</head><body><main><p>{text}</p>{links_html}</main></body></html>"
    return FetchedPage(url=url, title=title, html=html, text_hint=text, links=tuple(links), http_status=200, strategy=FetchStrategy.STATIC, metadata={"content_type": "text/html"})

class FakeFetcher:
    def __init__(self, pages, failures=None): self.pages, self.failures, self.calls = pages, failures or {}, []
    async def fetch(self, url, topic=None):
        self.calls.append((url, topic))
        if url in self.failures: raise self.failures[url]
        return self.pages[url]

def request(target, *, mode="domain", topic=None, max_pages=10, max_depth=2): return CrawlRequest((target,), topic, mode, max_pages, max_depth)

def test_url_mode_fetches_only_target_even_when_links_exist():
    root = "https://example.com/"; fetcher = FakeFetcher({root: make_page(root, links=("https://example.com/a",))})
    result = run(crawl(request(root, mode="url"), fetcher)); assert result.status == "completed"; assert result.attempted == 1; assert len(result.documents) == 1; assert [url for url, _ in fetcher.calls] == [root]

def test_domain_mode_is_breadth_first_bounded_and_same_registrable_domain():
    root="https://news.example.com/"; a="https://www.example.com/a"; b="https://example.com/b"; outside="https://other.test/outside"
    fetcher=FakeFetcher({root:make_page(root,links=(a,outside)),a:make_page(a,links=(b,)),b:make_page(b)})
    result=run(crawl(request(root,max_pages=2,max_depth=2),fetcher)); assert result.status=="completed"; assert result.attempted==2; assert [url for url,_ in fetcher.calls]==[root,a]; assert outside not in [url for url,_ in fetcher.calls]

def test_duplicate_fragment_links_are_queued_once():
    root="https://example.com/"; a="https://example.com/a"; fetcher=FakeFetcher({root:make_page(root,links=(a,a+"#one",a+"#two")),a:make_page(a)})
    result=run(crawl(request(root),fetcher)); assert result.attempted==2; assert [url for url,_ in fetcher.calls]==[root,a]

def test_same_canonical_document_is_persisted_once():
    root="https://example.com/"; a="https://example.com/a"; b="https://example.com/b"; canonical="https://example.com/story"
    fetcher=FakeFetcher({root:make_page(root,links=(a,b)),a:make_page(a,canonical=canonical,title="A"),b:make_page(b,canonical=canonical,title="B")})
    result=run(crawl(request(root,max_pages=3),fetcher)); assert result.attempted==3; assert [doc.canonical_url for doc in result.documents].count(canonical)==1; assert len(result.documents)==2

def test_child_failure_yields_partial_and_keeps_useful_documents():
    root="https://example.com/"; bad="https://example.com/bad"; good="https://example.com/good"; fetcher=FakeFetcher({root:make_page(root,links=(bad,good)),good:make_page(good)},failures={bad:RuntimeError("boom")})
    result=run(crawl(request(root),fetcher)); assert result.status=="partial"; assert result.attempted==3; assert len(result.documents)==2; assert result.failures[0].url==bad; assert result.failures[0].category=="fetch_error"; assert "boom" in result.failures[0].message

def test_root_failure_yields_failed_run_instead_of_false_completed():
    root="https://example.com/"; fetcher=FakeFetcher({},failures={root:RuntimeError("root down")}); result=run(crawl(request(root),fetcher)); assert result.status=="failed"; assert result.attempted==1; assert result.documents==(); assert result.failures[0].url==root

def test_topic_tokens_prioritize_relevant_sibling_within_same_depth():
    root="https://example.com/"; boring="https://example.com/about"; relevant="https://example.com/project-atlas-report"; fetcher=FakeFetcher({root:make_page(root,links=(boring,relevant)),boring:make_page(boring),relevant:make_page(relevant)})
    result=run(crawl(request(root,topic="Project Atlas Report",max_pages=2),fetcher)); assert result.attempted==2; assert [url for url,_ in fetcher.calls]==[root,relevant]

def test_depth_zero_never_follows_children():
    root="https://example.com/"; child="https://example.com/a"; fetcher=FakeFetcher({root:make_page(root,links=(child,))}); result=run(crawl(request(root,max_depth=0),fetcher)); assert result.attempted==1; assert [url for url,_ in fetcher.calls]==[root]
