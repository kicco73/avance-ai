from __future__ import annotations

import csv
import io
import json
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

from .crawler import CrawledPage, WebCrawler

if TYPE_CHECKING:
    from ai import AiService

MAX_CORPUS_CHARS = 60000
MIN_COLUMNS = 1
MAX_COLUMNS = 12
_FENCE_RE = re.compile(r"^\s*```[a-zA-Z]*\s*\n(.*?)\n?\s*```\s*$", re.DOTALL)


@dataclass(frozen=True)
class WebCorpus:
    """What one query brought back, in the shape a prompt wants it."""

    query: str
    pages: tuple[CrawledPage, ...]

    @property
    def text(self) -> str:
        documents = [f"### {page.title or page.url}\n{page.url}\n{page.text}" for page in self.pages]
        return "\n\n".join(documents)[:MAX_CORPUS_CHARS]


class WebSearch:
    """Searches the web for `query` and answers with CSV: the crawled
    pages decide the schema, then fill it. `csv_for` is the whole thing
    in one call; the three steps it composes are public so a caller that
    reports progress can run them one at a time."""

    def __init__(self, ai_service: "AiService", crawler: WebCrawler | None = None) -> None:
        self._ai_service = ai_service
        self._crawler = crawler if crawler is not None else WebCrawler()

    async def csv_for(self, query: str) -> str:
        corpus = await self.crawl(query)
        return await self.extract_csv(corpus, await self.extract_columns(corpus))

    async def crawl(self, query: str) -> WebCorpus:
        return WebCorpus(query=query, pages=tuple(await self._crawler.crawl(query)))

    async def extract_columns(self, corpus: WebCorpus) -> list[str]:
        reply = await self._ai_service.prompt(
            f"Web pages found for the search query: \"{corpus.query}\".\n"
            "Decide which tabular schema best describes the data these pages actually contain.\n"
            f"Answer with a JSON array of between {MIN_COLUMNS} and {MAX_COLUMNS} snake_case field names, "
            "and nothing else.\n\n"
            f"{corpus.text}"
        )
        return self.parse_columns(reply)

    async def extract_csv(self, corpus: WebCorpus, columns: list[str]) -> str:
        reply = await self._ai_service.prompt(
            f"Web pages found for the search query: \"{corpus.query}\".\n"
            f"Extract every record they describe as CSV with exactly these columns, in this order: "
            f"{','.join(columns)}.\n"
            "The first row must be that header. Leave a cell empty when the pages do not state its value, "
            "never invent one. Answer with CSV only, no commentary.\n\n"
            f"{corpus.text}"
        )
        return self.normalize_csv(reply, columns)

    @staticmethod
    def parse_columns(reply: str) -> list[str]:
        text = strip_fence(reply)
        start, end = text.find("["), text.rfind("]")
        if start == -1 or end <= start:
            raise ValueError("The model did not return a schema for the crawled content.")
        try:
            parsed = json.loads(text[start:end + 1])
        except json.JSONDecodeError as exc:
            raise ValueError(f"The model returned an unreadable schema: {exc}") from exc
        columns = [str(field).strip() for field in parsed if str(field).strip()] if isinstance(parsed, list) else []
        if not columns:
            raise ValueError("The model returned an empty schema for the crawled content.")
        return columns[:MAX_COLUMNS]

    @staticmethod
    def normalize_csv(reply: str, columns: list[str]) -> str:
        rows = [row for row in csv.reader(io.StringIO(strip_fence(reply))) if any(cell.strip() for cell in row)]
        if rows and [cell.strip().lower() for cell in rows[0]] == [column.lower() for column in columns]:
            rows = rows[1:]
        output = io.StringIO()
        writer = csv.writer(output, lineterminator="\n")
        writer.writerow(columns)
        for row in rows:
            writer.writerow([cell.strip() for cell in row[:len(columns)]] + [""] * (len(columns) - len(row)))
        return output.getvalue()


def strip_fence(reply: str) -> str:
    match = _FENCE_RE.match(reply or "")
    return match.group(1) if match else (reply or "").strip()
