from __future__ import annotations

import asyncio
import csv
import io
import json
import re
import uuid
from typing import TYPE_CHECKING

from jobs import CancelableJob
from session import Session

from .types import CommitCallback
from .web_import_crawler import CrawledPage

if TYPE_CHECKING:
    from ai import AiService

    from .editor import ProjectEditor
    from .web_import_crawler import WebCrawler

MAX_CORPUS_CHARS = 60000
MIN_COLUMNS = 1
MAX_COLUMNS = 12
_FENCE_RE = re.compile(r"^\s*```[a-zA-Z]*\s*\n(.*?)\n?\s*```\s*$", re.DOTALL)


class WebImportJob(CancelableJob):

    def __init__(
        self, editor: "ProjectEditor", ai_service: "AiService", crawler: "WebCrawler",
        loop: asyncio.AbstractEventLoop, project_id: str, source_name: str, archive_name: str,
        query: str, commit: CommitCallback,
    ) -> None:
        super().__init__(key="web-import", username=f"web-import:{uuid.uuid4().hex}")
        self._editor = editor
        self._ai_service = ai_service
        self._crawler = crawler
        self._loop = loop
        self._project_id = project_id
        self._source_name = source_name
        self._archive_name = archive_name
        self._query = query
        self._commit = commit
        self._owner = Session().user
        self._pages: list[CrawledPage] | None = None
        self._columns: list[str] | None = None
        self._csv: str | None = None
        self._rows = 0

    def _prepare(self) -> tuple[int, tuple[CancelableJob, ...]]:
        return 4, ()

    @property
    def is_background(self) -> bool:
        return False

    @property
    def result(self) -> str | None:
        return json.dumps({
            "success": True,
            "project_id": self._project_id,
            "source": self._source_name,
            "file_name": self._archive_name,
            "columns": self._columns or [],
            "rows": self._rows,
        })

    async def _run_next_step(self) -> None:
        if self._pages is None:
            self._pages = await self._crawler.crawl(self._query)
        elif self._columns is None:
            self._columns = await self._extract_schema()
        elif self._csv is None:
            self._csv = await self._extract_csv()
        else:
            await self._import_csv()

    def _corpus(self) -> str:
        assert self._pages is not None
        documents = [f"### {page.title or page.url}\n{page.url}\n{page.text}" for page in self._pages]
        return "\n\n".join(documents)[:MAX_CORPUS_CHARS]

    async def _extract_schema(self) -> list[str]:
        reply = await self._ai_service.prompt(
            f"Web pages found for the search query: \"{self._query}\".\n"
            "Decide which tabular schema best describes the data these pages actually contain.\n"
            f"Answer with a JSON array of between {MIN_COLUMNS} and {MAX_COLUMNS} snake_case field names, "
            "and nothing else.\n\n"
            f"{self._corpus()}"
        )
        return self._parse_columns(reply)

    @staticmethod
    def _parse_columns(reply: str) -> list[str]:
        text = _strip_fence(reply)
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

    async def _extract_csv(self) -> str:
        assert self._columns is not None
        columns = ",".join(self._columns)
        reply = await self._ai_service.prompt(
            f"Web pages found for the search query: \"{self._query}\".\n"
            f"Extract every record they describe as CSV with exactly these columns, in this order: {columns}.\n"
            "The first row must be that header. Leave a cell empty when the pages do not state its value, "
            "never invent one. Answer with CSV only, no commentary.\n\n"
            f"{self._corpus()}"
        )
        return self._normalize_csv(reply, self._columns)

    @staticmethod
    def _normalize_csv(reply: str, columns: list[str]) -> str:
        rows = [row for row in csv.reader(io.StringIO(_strip_fence(reply))) if any(cell.strip() for cell in row)]
        if rows and [cell.strip().lower() for cell in rows[0]] == [column.lower() for column in columns]:
            rows = rows[1:]
        output = io.StringIO()
        writer = csv.writer(output, lineterminator="\n")
        writer.writerow(columns)
        for row in rows:
            writer.writerow([cell.strip() for cell in row[:len(columns)]] + [""] * (len(columns) - len(row)))
        return output.getvalue()

    async def _import_csv(self) -> None:
        # FIXME: the write must run on the request's own loop — the chat
        # write lock commit takes is an asyncio primitive bound to it,
        # and this step runs on a job worker's separate loop.
        assert self._csv is not None and self._columns is not None
        future = asyncio.run_coroutine_threadsafe(self._put_archive(self._csv), self._loop)
        await asyncio.wrap_future(future)
        self._rows = max(len(self._csv.strip().split("\n")) - 1, 0)

    async def _put_archive(self, content: str) -> None:
        with Session().impersonate(self._owner):
            await self._editor.put_project_file(self._project_id, self._archive_name, content, None, self._commit)


def _strip_fence(reply: str) -> str:
    match = _FENCE_RE.match(reply or "")
    return match.group(1) if match else (reply or "").strip()
