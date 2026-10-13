from __future__ import annotations

import asyncio
import inspect
import socket
from dataclasses import dataclass
from html.parser import HTMLParser
from typing import Awaitable, Callable
from urllib.parse import urljoin, urlsplit

from .models import FetchStrategy, FetchedPage
from .security import validate_public_url

MIN_USEFUL_TEXT = 120
_BLOCK_MARKERS = (
    "please enable javascript",
    "javascript is required",
    "please turn javascript on",
    "checking your browser",
    "verify you are human",
    "attention required",
    "cf-chl-",
)
_RETRYABLE_HTTP = {401, 403, 408, 425, 429}


@dataclass(frozen=True)
class AcquisitionAttempt:
    strategy: FetchStrategy
    category: str
    message: str


class AcquisitionError(RuntimeError):
    def __init__(self, category: str, message: str, *, strategy: FetchStrategy | None = None):
        super().__init__(message)
        self.category = category
        self.strategy = strategy


class ForbiddenNetworkTarget(AcquisitionError):
    def __init__(self, message: str):
        super().__init__("forbidden_network_target", message)


class FetchExhaustedError(AcquisitionError):
    def __init__(self, attempts: tuple[AcquisitionAttempt, ...]):
        final = attempts[-1].message if attempts else "no acquisition strategy succeeded"
        super().__init__("acquisition_exhausted", f"All acquisition strategies failed: {final}")
        self.attempts = attempts


class _HTMLSummaryParser(HTMLParser):
    def __init__(self, base_url: str):
        super().__init__(convert_charrefs=True)
        self.base_url = base_url
        self.title_parts: list[str] = []
        self.text_parts: list[str] = []
        self.links: list[str] = []
        self._title_depth = 0
        self._ignored_depth = 0

    def handle_starttag(self, tag: str, attrs):
        tag = tag.lower()
        if tag in {"script", "style", "noscript", "template"}:
            self._ignored_depth += 1
        if tag == "title":
            self._title_depth += 1
        if tag == "a":
            href = next((value for key, value in attrs if key.lower() == "href"), None)
            if href:
                absolute = urljoin(self.base_url, href)
                if urlsplit(absolute).scheme in {"http", "https"}:
                    self.links.append(absolute)

    def handle_endtag(self, tag: str):
        tag = tag.lower()
        if tag == "title" and self._title_depth:
            self._title_depth -= 1
        if tag in {"script", "style", "noscript", "template"} and self._ignored_depth:
            self._ignored_depth -= 1

    def handle_data(self, data: str):
        if self._ignored_depth:
            return
        text = " ".join(data.split())
        if not text:
            return
        if self._title_depth:
            self.title_parts.append(text)
        self.text_parts.append(text)

    @property
    def title(self) -> str:
        return " ".join(self.title_parts).strip()

    @property
    def text(self) -> str:
        return " ".join(self.text_parts).strip()


def should_escalate(page: FetchedPage) -> bool:
    status = page.http_status
    if status in _RETRYABLE_HTTP or status >= 500:
        return True
    if status >= 400:
        return False
    text = " ".join(page.text_hint.split()).strip()
    lowered = f"{page.title} {text} {page.html}".lower()
    if any(marker in lowered for marker in _BLOCK_MARKERS):
        return True
    return len(text) < MIN_USEFUL_TEXT


class _AsyncNetworkGuard:
    def __init__(self, resolver):
        self._resolver = resolver
        self._lock = asyncio.Lock()

    async def ensure_url(self, url: str) -> str:
        parsed = urlsplit(url)
        if parsed.scheme not in {"http", "https"}:
            return url
        async with self._lock:
            return await asyncio.to_thread(validate_public_url, url, self._resolver)


FetchCallable = Callable[[str], object | Awaitable[object]]


class ScraplingFetcher:
    def __init__(
        self,
        *,
        static_fetch: FetchCallable | None = None,
        dynamic_fetch: FetchCallable | None = None,
        stealth_fetch: FetchCallable | None = None,
        resolver=socket.getaddrinfo,
    ):
        self.resolver = resolver
        self._network_guard = _AsyncNetworkGuard(resolver)
        self._static_fetch = static_fetch or self._default_static_fetch
        self._dynamic_fetch = dynamic_fetch or self._default_dynamic_fetch
        self._stealth_fetch = stealth_fetch or self._default_stealth_fetch

    def _validate_url(self, url: str) -> str:
        try:
            return validate_public_url(url, resolver=self.resolver)
        except ValueError as exc:
            raise ForbiddenNetworkTarget(str(exc)) from exc

    async def _resolve_result(self, operation: FetchCallable, url: str) -> object:
        result = operation(url)
        if inspect.isawaitable(result):
            return await result
        return result

    def _response_to_page(self, response: object, strategy: FetchStrategy) -> FetchedPage:
        if isinstance(response, FetchedPage):
            final_url = self._validate_url(response.url)
            if final_url == response.url and response.strategy is strategy:
                return response
            return FetchedPage(
                url=final_url,
                title=response.title,
                html=response.html,
                text_hint=response.text_hint,
                links=response.links,
                http_status=response.http_status,
                strategy=strategy,
                metadata=response.metadata,
            )

        for redirect in getattr(response, "history", ()) or ():
            redirect_url = getattr(redirect, "url", None)
            if redirect_url:
                self._validate_url(str(redirect_url))

        final_url = self._validate_url(str(getattr(response, "url", "")))
        html = str(getattr(response, "html_content", "") or "")
        parser = _HTMLSummaryParser(final_url)
        parser.feed(html)
        status = int(getattr(response, "status", 0) or 0)
        headers = getattr(response, "headers", {}) or {}
        content_type = ""
        try:
            content_type = str(headers.get("content-type") or headers.get("Content-Type") or "")
        except AttributeError:
            content_type = ""
        return FetchedPage(
            url=final_url,
            title=parser.title,
            html=html,
            text_hint=parser.text,
            links=tuple(dict.fromkeys(parser.links)),
            http_status=status,
            strategy=strategy,
            metadata={"content_type": content_type},
        )

    async def _invoke(self, operation: FetchCallable, url: str, strategy: FetchStrategy) -> FetchedPage:
        raw = await self._resolve_result(operation, url)
        return self._response_to_page(raw, strategy)

    async def fetch(self, url: str, topic: str | None = None) -> FetchedPage:
        del topic
        target = self._validate_url(url)
        attempts: list[AcquisitionAttempt] = []
        strategies = (
            (FetchStrategy.STATIC, self._static_fetch),
            (FetchStrategy.DYNAMIC, self._dynamic_fetch),
            (FetchStrategy.STEALTH, self._stealth_fetch),
        )

        for strategy, operation in strategies:
            try:
                page = await self._invoke(operation, target, strategy)
            except ForbiddenNetworkTarget:
                raise
            except Exception as exc:
                attempts.append(AcquisitionAttempt(strategy, "fetch_error", str(exc)))
                continue

            if page.http_status >= 400 and not should_escalate(page):
                raise AcquisitionError("http_failure", f"HTTP {page.http_status} from {page.url}", strategy=strategy)

            if should_escalate(page):
                category = "blocked" if page.http_status in _RETRYABLE_HTTP or page.http_status >= 500 else "insufficient_content"
                attempts.append(AcquisitionAttempt(strategy, category, f"{category} using {strategy.value}"))
                continue
            return page

        raise FetchExhaustedError(tuple(attempts))

    async def _default_static_fetch(self, url: str):
        from scrapling.fetchers import AsyncFetcher

        current = url
        for _ in range(6):
            response = await AsyncFetcher.get(current, follow_redirects=False, max_redirects=0, timeout=20, retries=2)
            status = int(getattr(response, "status", 0) or 0)
            if status not in {301, 302, 303, 307, 308}:
                return response
            headers = getattr(response, "headers", {}) or {}
            location = headers.get("location") or headers.get("Location")
            if not location:
                return response
            current = self._validate_url(urljoin(current, str(location)))
        raise AcquisitionError("http_failure", "Too many redirects", strategy=FetchStrategy.STATIC)

    async def _browser_page_setup(self, page):
        async def route_handler(route, request):
            target = str(request.url)
            if urlsplit(target).scheme in {"http", "https"}:
                try:
                    await self._network_guard.ensure_url(target)
                except ValueError:
                    await route.abort("blockedbyclient")
                    return
            await route.continue_()

        await page.route("**/*", route_handler)
        return page

    async def _default_dynamic_fetch(self, url: str):
        from scrapling.fetchers import DynamicFetcher

        return await DynamicFetcher.async_fetch(url, headless=True, disable_resources=True, page_setup=self._browser_page_setup, timeout=30_000, retries=1)

    async def _default_stealth_fetch(self, url: str):
        from scrapling.fetchers import StealthyFetcher

        return await StealthyFetcher.async_fetch(url, headless=True, disable_resources=True, page_setup=self._browser_page_setup, solve_cloudflare=False, timeout=30_000, retries=1)
