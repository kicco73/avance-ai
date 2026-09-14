from __future__ import annotations

import asyncio
import json
import uuid
from typing import TYPE_CHECKING

from jobs import CancelableJob
from system.web_session import WebSession
from websearch import WebCorpus

if TYPE_CHECKING:
    from websearch import WebSearch

    from .editor import ProjectEditor


class WebImportJob(CancelableJob):

    def __init__(
        self, editor: "ProjectEditor", web_search: "WebSearch", loop: asyncio.AbstractEventLoop,
        project_id: str, source_name: str, archive_name: str, query: str,
    ) -> None:
        super().__init__(key="web-import", username=f"web-import:{uuid.uuid4().hex}")
        self._editor = editor
        self._web_search = web_search
        self._loop = loop
        self._project_id = project_id
        self._source_name = source_name
        self._archive_name = archive_name
        self._query = query
        self._owner = WebSession().user
        self._corpus: WebCorpus | None = None
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
        if self._corpus is None:
            self._corpus = await self._web_search.crawl(self._query)
        elif self._columns is None:
            self._columns = await self._web_search.extract_columns(self._corpus)
        elif self._csv is None:
            self._csv = await self._web_search.extract_csv(self._corpus, self._columns)
        else:
            await self._import_csv()

    async def _import_csv(self) -> None:
        # FIXME: the write must run on the request's own loop — the chat
        assert self._csv is not None and self._columns is not None
        future = asyncio.run_coroutine_threadsafe(self._put_archive(self._csv), self._loop)
        await asyncio.wrap_future(future)
        self._rows = max(len(self._csv.strip().split("\n")) - 1, 0)

    async def _put_archive(self, content: str) -> None:
        with WebSession().impersonate(self._owner):
            await self._editor.put_project_file(self._project_id, self._archive_name, content, None)
