from __future__ import annotations

import asyncio
from dataclasses import dataclass
from html import unescape
from html.parser import HTMLParser
from urllib.parse import parse_qs, unquote, urlparse

import httpx

from logging_factory import LoggerFactory

logger = LoggerFactory.get_logger(__name__)

SEARCH_URL = "https://html.duckduckgo.com/html/"
USER_AGENT = "Mozilla/5.0 (compatible; AvanceWebImport/1.0)"
_IGNORED_HOSTS = ("duckduckgo.com", "duck.co")
_SKIPPED_TAGS = frozenset({"script", "style", "noscript", "template", "svg"})


@dataclass(frozen=True)
class CrawledPage:
    url: str
    title: str
    text: str


class _TextExtractor(HTMLParser):

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._chunks: list[str] = []
        self._titles: list[str] = []
        self._skipping = 0
        self._in_title = False

    def handle_starttag(self, tag, attrs):
        if tag in _SKIPPED_TAGS:
            self._skipping += 1
        elif tag == "title":
            self._in_title = True

    def handle_endtag(self, tag):
        if tag in _SKIPPED_TAGS and self._skipping:
            self._skipping -= 1
        elif tag == "title":
            self._in_title = False

    def handle_data(self, data):
        if self._skipping:
            return
        if self._in_title:
            self._titles.append(data)
        text = " ".join(data.split())
        if text:
            self._chunks.append(text)

    @property
    def title(self) -> str:
        return " ".join(" ".join(self._titles).split())

    @property
    def text(self) -> str:
        return "\n".join(self._chunks)


class _LinkCollector(HTMLParser):

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.urls: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag != "a":
            return
        href = dict(attrs).get("href")
        target = _resolve_result_url(href) if href else None
        if target is not None and target not in self.urls:
            self.urls.append(target)


def _resolve_result_url(href: str) -> str | None:
    href = unescape(href)
    if href.startswith("//"):
        href = f"https:{href}"
    parsed = urlparse(href)
    if parsed.path.startswith("/l/"):
        redirected = parse_qs(parsed.query).get("uddg")
        return unquote(redirected[0]) if redirected else None
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        return None
    if any(parsed.hostname == host or parsed.hostname.endswith(f".{host}") for host in _IGNORED_HOSTS):
        return None
    return href


class WebCrawler:

    def __init__(self, max_pages: int = 5, max_chars_per_page: int = 12000, timeout_seconds: float = 20.0) -> None:
        self._max_pages = max_pages
        self._max_chars_per_page = max_chars_per_page
        self._timeout_seconds = timeout_seconds

    async def crawl(self, query: str) -> list[CrawledPage]:
        async with httpx.AsyncClient(
            timeout=self._timeout_seconds, follow_redirects=True, headers={"User-Agent": USER_AGENT},
        ) as client:
            urls = await self._search(client, query)
            if not urls:
                raise ValueError(f"The web search for '{query}' returned no result to read.")
            fetched = await asyncio.gather(*(self._fetch(client, url) for url in urls))
        pages = [page for page in fetched if page is not None and page.text]
        if not pages:
            raise ValueError(f"None of the pages found for '{query}' could be read.")
        return pages

    async def _search(self, client: httpx.AsyncClient, query: str) -> list[str]:
        response = await client.post(SEARCH_URL, data={"q": query})
        response.raise_for_status()
        collector = _LinkCollector()
        collector.feed(response.text)
        return collector.urls[: self._max_pages]

    async def _fetch(self, client: httpx.AsyncClient, url: str) -> CrawledPage | None:
        try:
            response = await client.get(url)
            response.raise_for_status()
        except httpx.HTTPError as exc:
            logger.warning(f"Web import skipped '{url}': {exc}")
            return None
        if "html" not in response.headers.get("content-type", "text/html"):
            return None
        extractor = _TextExtractor()
        extractor.feed(response.text)
        return CrawledPage(url=url, title=extractor.title, text=extractor.text[: self._max_chars_per_page])
