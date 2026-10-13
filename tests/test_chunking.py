import pytest

from thiago_crawl.chunking import chunk_utf8


def test_empty_text_has_no_chunks():
    assert chunk_utf8("") == ()


def test_exact_default_boundary_stays_in_one_chunk():
    text = "a" * (512 * 1024)
    chunks = chunk_utf8(text)
    assert chunks == (text,)
    assert len(chunks[0].encode("utf-8")) == 512 * 1024


def test_unicode_chunks_preserve_exact_text_and_byte_limit():
    text = ("ação 🚀 çãõ — " * 100_000) + "fim"
    chunks = chunk_utf8(text, max_bytes=16_384)
    assert "".join(chunks) == text
    assert len(chunks) > 1
    assert all(len(chunk.encode("utf-8")) <= 16_384 for chunk in chunks)


def test_document_larger_than_two_mib_reconstructs_exactly():
    text = "á" * 1_100_000
    chunks = chunk_utf8(text)
    assert len(text.encode("utf-8")) > 2 * 1024 * 1024
    assert len(chunks) >= 5
    assert "".join(chunks) == text
    assert all(len(chunk.encode("utf-8")) <= 512 * 1024 for chunk in chunks)


def test_rejects_non_positive_limit_and_single_codepoint_larger_than_limit():
    with pytest.raises(ValueError, match="max_bytes"):
        chunk_utf8("x", max_bytes=0)
    with pytest.raises(ValueError, match="code point|character"):
        chunk_utf8("🚀", max_bytes=3)
