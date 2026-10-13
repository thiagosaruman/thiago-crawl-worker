from __future__ import annotations

import hashlib
from dataclasses import replace
from datetime import date, datetime
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit

from .models import FetchedPage, NormalizedDocument
from .security import normalize_url

MIN_NEWS_BODY = 120
_NOISE_TAGS = {"head", "script", "style", "noscript", "template", "nav", "header", "footer", "aside", "form", "svg"}
_VOID_TAGS = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}


def _normalize_text(text: str) -> str:
    return " ".join((text or "").split()).strip()


def content_hash(text: str) -> str:
    normalized = _normalize_text(text)
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


class _DocumentParser(HTMLParser):
    def __init__(self, base_url: str):
        super().__init__(convert_charrefs=True)
        self.base_url = base_url
        self.title_parts: list[str] = []
        self.text_parts: list[str] = []
        self.links: list[str] = []
        self.language: str | None = None
        self.canonical_url: str | None = None
        self.meta: dict[str, str] = {}
        self.has_article = False
        self._title_depth = 0
        self._ignore_stack: list[bool] = []

    @staticmethod
    def _attrs(attrs) -> dict[str, str]:
        return {str(key).lower(): "" if value is None else str(value) for key, value in attrs}

    @staticmethod
    def _is_hidden(attributes: dict[str, str]) -> bool:
        if "hidden" in attributes:
            return True
        if attributes.get("aria-hidden", "").lower() == "true":
            return True
        style = attributes.get("style", "").replace(" ", "").lower()
        return "display:none" in style or "visibility:hidden" in style

    def handle_starttag(self, tag: str, attrs):
        tag = tag.lower()
        attributes = self._attrs(attrs)
        parent_ignored = self._ignore_stack[-1] if self._ignore_stack else False
        ignored = parent_ignored or tag in _NOISE_TAGS or self._is_hidden(attributes)
        if tag not in _VOID_TAGS:
            self._ignore_stack.append(ignored)

        if tag == "html" and attributes.get("lang"):
            self.language = attributes["lang"].strip() or None
        if tag == "title":
            self._title_depth += 1
        if tag == "article":
            self.has_article = True
        if tag == "link":
            rel = attributes.get("rel", "").lower().split()
            href = attributes.get("href", "")
            if "canonical" in rel and href:
                self.canonical_url = urljoin(self.base_url, href)
        if tag == "meta":
            key = (attributes.get("property") or attributes.get("name") or "").strip().lower()
            value = attributes.get("content", "").strip()
            if key and value:
                self.meta[key] = value
        if tag == "a" and not ignored:
            href = attributes.get("href", "")
            if href:
                absolute = urljoin(self.base_url, href)
                if urlsplit(absolute).scheme in {"http", "https"}:
                    self.links.append(absolute)

    def handle_startendtag(self, tag: str, attrs):
        self.handle_starttag(tag, attrs)
        if tag.lower() not in _VOID_TAGS:
            self.handle_endtag(tag)

    def handle_endtag(self, tag: str):
        tag = tag.lower()
        if tag == "title" and self._title_depth:
            self._title_depth -= 1
        if self._ignore_stack:
            self._ignore_stack.pop()

    def handle_data(self, data: str):
        text = _normalize_text(data)
        if not text:
            return
        if self._title_depth:
            self.title_parts.append(text)
        if self._ignore_stack and self._ignore_stack[-1]:
            return
        self.text_parts.append(text)

    @property
    def title(self) -> str:
        return _normalize_text(" ".join(self.title_parts))

    @property
    def text(self) -> str:
        return _normalize_text(" ".join(self.text_parts))


def _parse_html(page: FetchedPage) -> _DocumentParser:
    parser = _DocumentParser(page.url)
    parser.feed(page.html or "")
    parser.close()
    return parser


def classify_document(page: FetchedPage) -> str:
    parser = _parse_html(page)
    path = urlsplit(page.url).path.lower()
    og_type = parser.meta.get("og:type", "").lower()
    html_lower = (page.html or "").lower()

    if parser.has_article or og_type in {"article", "news", "newsarticle"} or "article:published_time" in parser.meta or '"@type":"newsarticle"' in html_lower.replace(" ", ""):
        return "news"
    if any(marker in path for marker in ("/docs/", "/documentation/", "/manual/", "/reference/", "/guide/")):
        return "docs"
    if "documentation" in (parser.title or page.title).lower():
        return "docs"
    return "generic"


def _canonical(page: FetchedPage, candidate: str | None) -> str:
    target = urljoin(page.url, candidate) if candidate else page.url
    try:
        return normalize_url(target)
    except ValueError:
        return normalize_url(page.url)


def _normalized_links(page: FetchedPage, parser: _DocumentParser) -> tuple[str, ...]:
    output: list[str] = []
    for raw in (*page.links, *parser.links):
        try:
            normalized = normalize_url(urljoin(page.url, raw))
        except ValueError:
            continue
        if urlsplit(normalized).scheme in {"http", "https"} and normalized not in output:
            output.append(normalized)
    return tuple(output)


def extract_generic(page: FetchedPage) -> NormalizedDocument:
    parser = _parse_html(page)
    text = parser.text or _normalize_text(page.text_hint)
    title = parser.title or _normalize_text(page.title)
    document_type = classify_document(page)
    metadata: dict[str, object] = dict(page.metadata)
    if description := parser.meta.get("description"):
        metadata["description"] = description
    return NormalizedDocument(
        canonical_url=_canonical(page, parser.canonical_url),
        document_type=document_type,
        title=title,
        authors=(),
        published_at=parser.meta.get("article:published_time"),
        language=parser.language,
        text_content=text,
        content_hash=content_hash(text),
        links=_normalized_links(page, parser),
        fetch_strategy=page.strategy,
        extractor="generic",
        metadata=metadata,
    )


def _newspaper_language(page: FetchedPage) -> str:
    parser = _parse_html(page)
    raw = (parser.language or "pt").strip().lower().replace("_", "-")
    primary = raw.split("-", 1)[0]
    return primary if len(primary) == 2 else "pt"


def _published_at(value) -> str | None:
    if value is None:
        return None
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    text = str(value).strip()
    return text or None


def _clean_authors(values) -> tuple[str, ...]:
    authors: list[str] = []
    for value in values or ():
        author = _normalize_text(str(value))
        if author and author not in authors:
            authors.append(author)
    return tuple(authors)


def extract_news(page: FetchedPage) -> NormalizedDocument:
    import newspaper

    language = _newspaper_language(page)
    article = newspaper.article(
        page.url,
        language=language,
        input_html=page.html,
        fetch_images=False,
    )
    article.parse()

    text = _normalize_text(getattr(article, "text", ""))
    article_title = _normalize_text(getattr(article, "title", ""))
    authors = _clean_authors(getattr(article, "authors", ()))
    published = _published_at(getattr(article, "publish_date", None))
    canonical_candidate = _normalize_text(getattr(article, "canonical_link", "")) or None

    if len(text) < MIN_NEWS_BODY:
        generic = extract_generic(page)
        metadata = dict(generic.metadata)
        metadata["news_fallback"] = True
        return replace(
            generic,
            document_type="news",
            title=article_title or generic.title,
            authors=authors,
            published_at=published or generic.published_at,
            canonical_url=_canonical(page, canonical_candidate or generic.canonical_url),
            metadata=metadata,
        )

    parser = _parse_html(page)
    metadata: dict[str, object] = dict(page.metadata)
    for attr, key in (
        ("meta_description", "description"),
        ("meta_site_name", "site_name"),
        ("top_image", "top_image"),
    ):
        value = _normalize_text(getattr(article, attr, ""))
        if value:
            metadata[key] = value

    article_language = _normalize_text(getattr(article, "meta_lang", "")) or parser.language or language
    return NormalizedDocument(
        canonical_url=_canonical(page, canonical_candidate or parser.canonical_url),
        document_type="news",
        title=article_title or parser.title or _normalize_text(page.title),
        authors=authors,
        published_at=published or parser.meta.get("article:published_time"),
        language=article_language,
        text_content=text,
        content_hash=content_hash(text),
        links=_normalized_links(page, parser),
        fetch_strategy=page.strategy,
        extractor="newspaper4k",
        metadata=metadata,
    )


def extract_document(page: FetchedPage) -> NormalizedDocument:
    if classify_document(page) == "news":
        return extract_news(page)
    return extract_generic(page)
