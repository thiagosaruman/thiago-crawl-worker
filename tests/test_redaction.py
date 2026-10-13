from thiago_crawl.redaction import redact_log_message

def test_redacts_absolute_urls_and_query_strings():
    message="fetch failed for https://example.com/private/path?token=abc&user=sample status=403"; redacted=redact_log_message(message); assert "https://" not in redacted; assert "token=abc" not in redacted; assert "sample" not in redacted; assert "status=403" in redacted; assert "[redacted-url]" in redacted

def test_redacts_bearer_and_cookie_values():
    message="Authorization: Bearer secret.jwt.value Cookie: session=abc123; csrftoken=def456 category=auth count=2"; redacted=redact_log_message(message); assert "secret.jwt.value" not in redacted; assert "abc123" not in redacted; assert "def456" not in redacted; assert "category=auth" in redacted; assert "count=2" in redacted

def test_redacts_control_plane_payload_fragments():
    message='{"job_id":"opaque-123","url":"https://news.example/a","topic":"Project Atlas","content":"page text","count":4}'; redacted=redact_log_message(message); assert "opaque-123" in redacted; assert "news.example" not in redacted; assert "Project Atlas" not in redacted; assert "page text" not in redacted; assert '"count":4' in redacted

def test_handles_exception_repr_and_unicode_without_leaking_target():
    message="RuntimeError('Falha ao buscar https://exemplo.com.br/ação?q=segredo') estratégia=stealth falhas=1"; redacted=redact_log_message(message); assert "exemplo.com.br" not in redacted; assert "segredo" not in redacted; assert "estratégia=stealth" in redacted; assert "falhas=1" in redacted

def test_preserves_safe_category_and_count_messages():
    message="crawl_terminal category=timeout attempted=7 succeeded=5 failed=2 strategy=dynamic"; assert redact_log_message(message)==message
