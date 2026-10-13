import asyncio
import socket
import pytest
from thiago_crawl.models import FetchStrategy, FetchedPage
from thiago_crawl.fetcher import AcquisitionError, FetchExhaustedError, ForbiddenNetworkTarget, ScraplingFetcher, should_escalate

def run(coro): return asyncio.run(coro)
def public_resolver(host,port,type=0): return [(socket.AF_INET,socket.SOCK_STREAM,6,"",("93.184.216.34",0))]
def make_page(*,strategy=FetchStrategy.STATIC,text=None,status=200,url="https://example.com/"):
    text=text if text is not None else ("Useful article text. "*20)
    return FetchedPage(url=url,title="Example",html=f"<html><head><title>Example</title></head><body>{text}</body></html>",text_hint=text,links=("https://example.com/a",),http_status=status,strategy=strategy,metadata={"content_type":"text/html"})
class FakeStrategy:
    def __init__(self,result=None,error=None): self.result,self.error,self.calls=result,error,[]
    async def __call__(self,url):
        self.calls.append(url)
        if self.error: raise self.error
        return self.result

def test_static_success_stops_escalation():
    static=FakeStrategy(make_page()); dynamic=FakeStrategy(make_page(strategy=FetchStrategy.DYNAMIC)); stealth=FakeStrategy(make_page(strategy=FetchStrategy.STEALTH)); fetcher=ScraplingFetcher(static_fetch=static,dynamic_fetch=dynamic,stealth_fetch=stealth,resolver=public_resolver); page=run(fetcher.fetch("https://example.com/")); assert page.strategy is FetchStrategy.STATIC; assert static.calls==["https://example.com/"]; assert dynamic.calls==[]; assert stealth.calls==[]
def test_empty_static_escalates_to_dynamic():
    static=FakeStrategy(make_page(text="Loading...")); dynamic=FakeStrategy(make_page(strategy=FetchStrategy.DYNAMIC)); stealth=FakeStrategy(make_page(strategy=FetchStrategy.STEALTH)); fetcher=ScraplingFetcher(static_fetch=static,dynamic_fetch=dynamic,stealth_fetch=stealth,resolver=public_resolver); page=run(fetcher.fetch("https://example.com/")); assert page.strategy is FetchStrategy.DYNAMIC; assert dynamic.calls==["https://example.com/"]; assert stealth.calls==[]
def test_dynamic_block_page_escalates_to_stealth():
    static=FakeStrategy(make_page(text="Please enable JavaScript to continue")); dynamic=FakeStrategy(make_page(strategy=FetchStrategy.DYNAMIC,text="Checking your browser before accessing the site")); stealth=FakeStrategy(make_page(strategy=FetchStrategy.STEALTH)); fetcher=ScraplingFetcher(static_fetch=static,dynamic_fetch=dynamic,stealth_fetch=stealth,resolver=public_resolver); page=run(fetcher.fetch("https://example.com/")); assert page.strategy is FetchStrategy.STEALTH; assert stealth.calls==["https://example.com/"]
def test_three_strategy_failures_raise_classified_exhaustion():
    static=FakeStrategy(error=TimeoutError("static timed out")); dynamic=FakeStrategy(error=RuntimeError("browser failed")); stealth=FakeStrategy(error=RuntimeError("stealth failed")); fetcher=ScraplingFetcher(static_fetch=static,dynamic_fetch=dynamic,stealth_fetch=stealth,resolver=public_resolver)
    with pytest.raises(FetchExhaustedError) as exc: run(fetcher.fetch("https://example.com/"))
    assert exc.value.category=="acquisition_exhausted"; assert [a.strategy for a in exc.value.attempts]==[FetchStrategy.STATIC,FetchStrategy.DYNAMIC,FetchStrategy.STEALTH]; assert "stealth failed" in str(exc.value)
def test_private_redirect_result_is_terminal_security_error():
    static=FakeStrategy(make_page(url="http://127.0.0.1/private")); dynamic=FakeStrategy(make_page(strategy=FetchStrategy.DYNAMIC)); fetcher=ScraplingFetcher(static_fetch=static,dynamic_fetch=dynamic,stealth_fetch=FakeStrategy(),resolver=public_resolver)
    with pytest.raises(ForbiddenNetworkTarget): run(fetcher.fetch("https://example.com/"))
    assert dynamic.calls==[]
def test_non_retryable_http_error_does_not_open_browser():
    static=FakeStrategy(make_page(status=404,text="Not found")); dynamic=FakeStrategy(make_page(strategy=FetchStrategy.DYNAMIC)); fetcher=ScraplingFetcher(static_fetch=static,dynamic_fetch=dynamic,stealth_fetch=FakeStrategy(),resolver=public_resolver)
    with pytest.raises(AcquisitionError) as exc: run(fetcher.fetch("https://example.com/"))
    assert exc.value.category=="http_failure"; assert dynamic.calls==[]
@pytest.mark.parametrize(("page","expected"),[(make_page(),False),(make_page(text="short"),True),(make_page(text="Please enable JavaScript to continue"),True),(make_page(text="Checking your browser before accessing"),True),(make_page(status=403),True),(make_page(status=429),True),(make_page(status=503),True),(make_page(status=404),False)])
def test_should_escalate(page,expected): assert should_escalate(page) is expected
def test_network_guard_rechecks_same_hostname_to_detect_dns_rebinding():
    answers=iter(["93.184.216.34","10.0.0.9"])
    def rotating_resolver(host,port,type=0): return [(socket.AF_INET,socket.SOCK_STREAM,6,"",(next(answers),0))]
    fetcher=ScraplingFetcher(static_fetch=FakeStrategy(make_page()),dynamic_fetch=FakeStrategy(),stealth_fetch=FakeStrategy(),resolver=rotating_resolver); run(fetcher._network_guard.ensure_url("https://example.com/a"))
    with pytest.raises(ValueError,match="private|non-public"): run(fetcher._network_guard.ensure_url("https://example.com/b"))
def test_default_stealth_path_never_enables_cloudflare_solver(monkeypatch):
    import sys, types
    captured={}
    class FakeStealthyFetcher:
        @classmethod
        async def async_fetch(cls,url,**kwargs): captured.update(kwargs); return make_page(strategy=FetchStrategy.STEALTH,url=url)
    scrapling_module=types.ModuleType("scrapling"); fetchers_module=types.ModuleType("scrapling.fetchers"); fetchers_module.StealthyFetcher=FakeStealthyFetcher; monkeypatch.setitem(sys.modules,"scrapling",scrapling_module); monkeypatch.setitem(sys.modules,"scrapling.fetchers",fetchers_module); fetcher=ScraplingFetcher(resolver=public_resolver); page=run(fetcher._default_stealth_fetch("https://example.com/")); assert page.strategy is FetchStrategy.STEALTH; assert captured["solve_cloudflare"] is False
