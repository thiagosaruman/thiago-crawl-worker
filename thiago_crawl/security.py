from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlsplit, urlunsplit

MAX_PAGES_HARD = 50
MAX_DEPTH_HARD = 2
_PRIVATE_HOST_SUFFIXES = (".localhost", ".local", ".internal", ".home", ".lan")
_COMMON_SECOND_LEVEL = {"ac", "co", "com", "edu", "gov", "mil", "net", "nom", "org", "sch"}


def normalize_url(url: str) -> str:
    parsed = urlsplit(url.strip())
    scheme = parsed.scheme.lower()
    host = (parsed.hostname or "").lower()
    if not scheme or not host:
        return url.strip()
    try:
        port = parsed.port
    except ValueError as exc:
        raise ValueError(f"Invalid URL port: {url}") from exc
    display_host = f"[{host}]" if ":" in host and not host.startswith("[") else host
    default_port = (scheme == "http" and port == 80) or (scheme == "https" and port == 443)
    netloc = display_host if port is None or default_port else f"{display_host}:{port}"
    return urlunsplit((scheme, netloc, parsed.path or "/", parsed.query, ""))


def _assert_global_address(address_text: str, host: str) -> None:
    address = ipaddress.ip_address(address_text.split("%", 1)[0])
    if not address.is_global:
        raise ValueError(f"Hostname resolves to a private or non-public address: {host}")


def _assert_public_resolution(host: str, resolver) -> None:
    try:
        answers = resolver(host, None, type=socket.SOCK_STREAM)
    except OSError as exc:
        raise ValueError(f"Hostname could not be resolved: {host}") from exc
    if not answers:
        raise ValueError(f"Hostname could not be resolved: {host}")
    for answer in answers:
        _assert_global_address(str(answer[4][0]), host)


def validate_public_url(url: str, resolver=socket.getaddrinfo) -> str:
    raw = urlsplit(url.strip())
    if raw.username or raw.password:
        raise ValueError("URLs with embedded credentials are not allowed")
    normalized = normalize_url(url)
    parsed = urlsplit(normalized)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("Provide an absolute http(s) URL")
    host = parsed.hostname.rstrip(".").lower()
    if host == "localhost" or host.endswith(_PRIVATE_HOST_SUFFIXES):
        raise ValueError("Private or local network targets are not allowed")
    try:
        literal = ipaddress.ip_address(host)
    except ValueError:
        literal = None
    if literal is not None:
        if not literal.is_global:
            raise ValueError("Private or non-public IP targets are not allowed")
    else:
        _assert_public_resolution(host, resolver)
    return normalized


def registrable_domain(url: str) -> str:
    host = (urlsplit(url).hostname or "").rstrip(".").lower()
    if not host:
        raise ValueError("URL has no hostname")
    try:
        import tldextract
    except ImportError:
        tldextract = None
    if tldextract is not None:
        extractor = tldextract.TLDExtract(suffix_list_urls=(), include_psl_private_domains=True)
        parts = extractor(host)
        domain = parts.top_domain_under_public_suffix
        if domain:
            return domain
    labels = host.split(".")
    if len(labels) <= 2:
        return host
    if len(labels[-1]) == 2 and labels[-2] in _COMMON_SECOND_LEVEL and len(labels) >= 3:
        return ".".join(labels[-3:])
    return ".".join(labels[-2:])


def validate_limits(max_pages: int, max_depth: int) -> tuple[int, int]:
    if not 1 <= max_pages <= MAX_PAGES_HARD:
        raise ValueError(f"max_pages must be between 1 and {MAX_PAGES_HARD}")
    if not 0 <= max_depth <= MAX_DEPTH_HARD:
        raise ValueError(f"max_depth must be between 0 and {MAX_DEPTH_HARD}")
    return max_pages, max_depth
