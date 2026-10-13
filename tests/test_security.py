import socket
import pytest
from thiago_crawl.security import normalize_url, registrable_domain, validate_limits, validate_public_url

def public_resolver(host,port,type=0): return [(socket.AF_INET,socket.SOCK_STREAM,6,"",("93.184.216.34",0))]
def private_resolver(host,port,type=0): return [(socket.AF_INET,socket.SOCK_STREAM,6,"",("10.20.30.40",0))]
def ipv6_private_resolver(host,port,type=0): return [(socket.AF_INET6,socket.SOCK_STREAM,6,"",("fd00::1",0,0,0))]
def test_validate_public_url_normalizes_public_http_urls(): assert validate_public_url("https://Example.COM:443/a?x=1#frag",resolver=public_resolver)=="https://example.com/a?x=1"; assert validate_public_url("http://Example.com:80",resolver=public_resolver)=="http://example.com/"
@pytest.mark.parametrize("url",["ftp://example.com/file","example.com","http://localhost/","http://127.0.0.1/","http://169.254.169.254/latest/meta-data/","http://10.0.0.1/","http://192.168.1.1/","http://[::1]/","https://user:pass@example.com/private"])
def test_validate_public_url_rejects_non_public_or_credential_targets(url):
    with pytest.raises(ValueError): validate_public_url(url,resolver=public_resolver)
def test_validate_public_url_rejects_private_dns_answers():
    with pytest.raises(ValueError,match="private|non-public"): validate_public_url("https://public-looking.example/",resolver=private_resolver)
    with pytest.raises(ValueError,match="private|non-public"): validate_public_url("https://public-looking.example/",resolver=ipv6_private_resolver)
def test_validate_public_url_rejects_mixed_public_private_dns_answers():
    def mixed_resolver(host,port,type=0): return [(socket.AF_INET,socket.SOCK_STREAM,6,"",("93.184.216.34",0)),(socket.AF_INET,socket.SOCK_STREAM,6,"",("10.0.0.2",0))]
    with pytest.raises(ValueError,match="private|non-public"): validate_public_url("https://mixed.example/",resolver=mixed_resolver)
def test_normalize_url_drops_fragment_and_default_port(): assert normalize_url("HTTPS://Example.COM:443/a#x")=="https://example.com/a"
def test_registrable_domain_supports_common_multilevel_suffixes_without_network(): assert registrable_domain("https://news.bbc.co.uk/story")=="bbc.co.uk"; assert registrable_domain("https://sub.example.com/a")=="example.com"
def test_validate_limits_enforces_v1_bounds():
    assert validate_limits(50,2)==(50,2)
    for pages,depth in [(0,1),(51,1),(10,-1),(10,3)]:
        with pytest.raises(ValueError): validate_limits(pages,depth)
