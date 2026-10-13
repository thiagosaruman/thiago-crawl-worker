import datetime as dt
import sys
import types
from pathlib import Path

import pytest
from thiago_crawl.extractors import classify_document, content_hash, extract_document, extract_generic, extract_news
from thiago_crawl.models import FetchStrategy, FetchedPage

FIXTURES = Path(__file__).parent / "fixtures"

def page_from(name: str, url: str) -> FetchedPage:
    html=(FIXTURES/name).read_text(encoding="utf-8")
    return FetchedPage(url=url,title="Fetcher title",html=html,text_hint="hint",links=(url+"#self","https://example.com/linked"),http_status=200,strategy=FetchStrategy.STATIC,metadata={"content_type":"text/html; charset=utf-8"})

def install_fake_newspaper(monkeypatch, *, text, title="Extraído", authors=None, publish_date=None, canonical_link="", meta_lang="pt"):
    calls=[]
    class FakeArticle:
        def __init__(self):
            self.text=text; self.title=title; self.authors=authors or []; self.publish_date=publish_date; self.canonical_link=canonical_link; self.meta_lang=meta_lang; self.meta_description="Descrição extraída"; self.meta_site_name="Jornal Teste"; self.top_image="https://img.example.com/top.jpg"; self.parsed=False
        def parse(self): self.parsed=True
    def article(url,**kwargs):
        obj=FakeArticle(); calls.append((url,kwargs,obj)); return obj
    module=types.ModuleType("newspaper"); module.article=article; monkeypatch.setitem(sys.modules,"newspaper",module); return calls

def test_classify_news_docs_and_generic():
    news=page_from("news_pt.html","https://noticias.example.com/2026/10/10/mudanca"); docs=page_from("docs_page.html","https://docs.example.com/docs/atlas/admin")
    generic=FetchedPage(url="https://example.com/about",title="About",html="<html><body><main><p>About our company and services.</p></main></body></html>",text_hint="About our company and services.",links=(),http_status=200,strategy=FetchStrategy.STATIC,metadata={})
    assert classify_document(news)=="news"; assert classify_document(docs)=="docs"; assert classify_document(generic)=="generic"

def test_newspaper4k_receives_scrapling_html_without_network_download(monkeypatch):
    page=page_from("news_pt.html","https://noticias.example.com/2026/10/10/mudanca"); body="Texto jornalístico extraído. "*12
    calls=install_fake_newspaper(monkeypatch,text=body,title="Título Newspaper",authors=["Maria Repórter"],publish_date=dt.datetime(2026,10,10,12,30,tzinfo=dt.timezone(dt.timedelta(hours=-3))),canonical_link="https://noticias.example.com/2026/10/10/mudanca",meta_lang="pt")
    document=extract_news(page); assert len(calls)==1; url,kwargs,article=calls[0]; assert url==page.url; assert kwargs["input_html"]==page.html; assert kwargs["fetch_images"] is False; assert kwargs["language"]=="pt"; assert article.parsed is True; assert document.extractor=="newspaper4k"; assert document.document_type=="news"; assert document.title=="Título Newspaper"; assert document.authors==("Maria Repórter",); assert document.published_at=="2026-10-10T12:30:00-03:00"; assert document.text_content==body.strip(); assert document.content_hash==content_hash(body)

def test_short_newspaper_body_falls_back_to_generic_but_keeps_trustworthy_metadata(monkeypatch):
    page=page_from("news_missing_metadata.html","https://news.example.com/story"); install_fake_newspaper(monkeypatch,text="curto",title="Título confirmado",authors=["Autora Confirmada"],publish_date="2026-10-10",canonical_link="https://news.example.com/story")
    document=extract_news(page); assert document.extractor=="generic"; assert document.document_type=="news"; assert document.title=="Título confirmado"; assert document.authors==("Autora Confirmada",); assert document.published_at=="2026-10-10"; assert "texto completo disponível" in document.text_content.lower(); assert document.metadata["news_fallback"] is True

def test_generic_docs_removes_navigation_scripts_and_hidden_content():
    page=page_from("docs_page.html","https://docs.example.com/docs/atlas/admin"); document=extract_generic(page); assert document.document_type=="docs"; assert document.title=="Atlas Administration Guide"; assert document.language=="en"; assert "Configure the cluster" in document.text_content; assert "atlas configure --safe" in document.text_content; assert "Product menu" not in document.text_content; assert "Home Docs Pricing" not in document.text_content; assert "ignore previous instructions" not in document.text_content; assert "alert" not in document.text_content; assert document.canonical_url=="https://docs.example.com/docs/atlas/admin"

def test_malformed_utf8_html_is_recovered_without_corruption():
    page=page_from("malformed.html","https://example.com/quebrada"); document=extract_generic(page); assert document.title=="Página quebrada"; assert document.language=="pt-BR"; assert "Olá çãõ" in document.text_content; assert "continua aqui" in document.text_content

def test_content_hash_normalizes_whitespace_and_utf8_deterministically():
    assert content_hash("Olá   mundo\ncom café")==content_hash("  Olá mundo com café  "); assert content_hash("Olá mundo")!=content_hash("Ola mundo")

def test_extract_document_routes_to_news_and_generic(monkeypatch):
    news=page_from("news_pt.html","https://noticias.example.com/2026/10/10/mudanca"); install_fake_newspaper(monkeypatch,text="Corpo de notícia relevante. "*12); assert extract_document(news).document_type=="news"; docs=page_from("docs_page.html","https://docs.example.com/docs/atlas/admin"); assert extract_document(docs).document_type=="docs"


def test_normalized_document_preserves_origin_http_status(monkeypatch):
    generic = page_from("docs_page.html", "https://docs.example.com/docs/atlas/admin")
    generic = FetchedPage(
        url=generic.url,
        title=generic.title,
        html=generic.html,
        text_hint=generic.text_hint,
        links=generic.links,
        http_status=206,
        strategy=generic.strategy,
        metadata=generic.metadata,
    )
    assert extract_generic(generic).http_status == 206

    news = page_from("news_pt.html", "https://noticias.example.com/2026/10/10/mudanca")
    news = FetchedPage(
        url=news.url,
        title=news.title,
        html=news.html,
        text_hint=news.text_hint,
        links=news.links,
        http_status=203,
        strategy=news.strategy,
        metadata=news.metadata,
    )
    install_fake_newspaper(monkeypatch, text="Corpo de notícia relevante. " * 12)
    assert extract_news(news).http_status == 203
