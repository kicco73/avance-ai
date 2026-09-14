"""Tests for the Source card's own "AI Web Import" — POST
/api/skills/platform/projects/{id}/sources/{name}/web-import, its 4-step WebImportJob
(crawl, schema extraction, CSV extraction, import) and the SSE progress
the same response streams back, ending with the CSV written into the
source's own archive exactly as a manual upload would leave it. The
search itself is WebSearch's, and what it makes of a model's reply is
tested in backend/tests/test_websearch.py."""
from __future__ import annotations

import asyncio
import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from websearch import CrawledPage

pytestmark = pytest.mark.contract

PAGES = [
    CrawledPage(url="https://example.com/a", title="Dentists", text="Dr. Nuria — Eixample — 4.8"),
    CrawledPage(url="https://example.com/b", title="More dentists", text="Dr. Pau — Gracia — 4.6"),
]
COLUMNS = ["name", "district", "rating"]
MODEL_CSV = "name,district,rating\nDr. Nuria,Eixample,4.8\nDr. Pau,Gracia,4.6\n"
STEP_SECONDS = 0.15


class FakeCrawler:

    def __init__(self, pages: list[CrawledPage], step_seconds: float = 0.0) -> None:
        self.pages = pages
        self.step_seconds = step_seconds
        self.queries: list[str] = []

    async def crawl(self, query: str) -> list[CrawledPage]:
        self.queries.append(query)
        await asyncio.sleep(self.step_seconds)
        return self.pages


class FakeWebImportAi:

    def __init__(self, columns: list[str], csv_text: str, step_seconds: float = 0.0) -> None:
        self.columns = columns
        self.csv_text = csv_text
        self.step_seconds = step_seconds
        self.prompts: list[str] = []

    async def prompt(self, prompt: str, channels=None, tool_set=None):
        self.prompts.append(prompt)
        await asyncio.sleep(self.step_seconds)
        if "JSON array" in prompt:
            return f"```json\n{json.dumps(self.columns)}\n```"
        return self.csv_text


def _install_fakes(app: FastAPI, pages=None, columns=None, csv_text=None, step_seconds: float = 0.0):
    crawler = FakeCrawler(PAGES if pages is None else pages, step_seconds)
    ai_service = FakeWebImportAi(
        COLUMNS if columns is None else columns, MODEL_CSV if csv_text is None else csv_text, step_seconds,
    )
    project_service = app.state.project_service
    project_service.web_crawler = crawler
    project_service.ai_service = ai_service
    return crawler, ai_service


def _add_source(client: TestClient, project_id: str, name_hint: str = "places") -> str:
    response = client.post(f"/api/skills/platform/projects/{project_id}/sources?file_name={name_hint}", content=b"")
    assert response.status_code == 200, response.text
    return response.json()["name"]


def _web_import(client: TestClient, project_id: str, source_name: str, query: str):
    return client.post(f"/api/skills/platform/projects/{project_id}/sources/{source_name}/web-import", json={"query": query})


def _sse_messages(response) -> list[dict]:
    return [
        json.loads(line[len("data: "):])
        for line in response.text.strip().split("\n") if line.startswith("data: ")
    ]


def test_web_import_reports_one_quarter_per_step_and_writes_the_csv_into_the_source(app, client, hello_project):
    crawler, ai_service = _install_fakes(app, step_seconds=STEP_SECONDS)
    source_name = _add_source(client, hello_project)

    response = _web_import(client, hello_project, source_name, "well-reviewed dentists in Barcelona")

    assert response.status_code == 200, response.text
    messages = _sse_messages(response)
    final = messages[-1]
    assert final["queue_status"] == "exited" and final["job_status"] == "completed", response.text
    percentages = [message["percentage"] for message in messages]
    assert percentages[0] == 0.0 and percentages[-1] == 100.0
    assert set(percentages) <= {0.0, 25.0, 50.0, 75.0, 100.0}
    assert percentages == sorted(set(percentages))
    assert final["result"] == {
        "success": True, "project_id": hello_project, "source": source_name,
        "file_name": f"sources/{source_name}.csv", "columns": COLUMNS, "rows": 2,
    }
    assert crawler.queries == ["well-reviewed dentists in Barcelona"]
    assert len(ai_service.prompts) == 2

    stored = client.get(f"/api/skills/platform/projects/{hello_project}/files/sources/{source_name}.csv")
    assert stored.status_code == 200, stored.text
    assert stored.json()["content"] == MODEL_CSV


def test_web_import_normalizes_the_model_csv_against_the_extracted_schema(app, client, hello_project):
    _install_fakes(app, csv_text="```csv\nname,district,rating\nDr. Nuria,Eixample,4.8,ignored\n\nDr. Pau\n```")
    source_name = _add_source(client, hello_project)

    response = _web_import(client, hello_project, source_name, "dentists")

    assert response.status_code == 200, response.text
    stored = client.get(f"/api/skills/platform/projects/{hello_project}/files/sources/{source_name}.csv")
    assert stored.json()["content"] == "name,district,rating\nDr. Nuria,Eixample,4.8\nDr. Pau,,\n"


def test_web_import_of_an_unknown_source_or_an_empty_query_is_refused_before_any_job_runs(app, client, hello_project):
    crawler, _ = _install_fakes(app)
    source_name = _add_source(client, hello_project)

    assert _web_import(client, hello_project, "nope", "dentists").status_code == 404
    assert _web_import(client, hello_project, source_name, "   ").status_code == 400
    assert crawler.queries == []


def test_a_failing_step_ends_the_stream_as_a_failed_job_leaving_the_source_untouched(app, client, hello_project):
    _install_fakes(app, columns=[])
    source_name = _add_source(client, hello_project)

    response = _web_import(client, hello_project, source_name, "dentists")

    assert response.status_code == 200, response.text
    final = _sse_messages(response)[-1]
    assert final["queue_status"] == "exited" and final["job_status"] == "failed"
    assert final["error"]
    stored = client.get(f"/api/skills/platform/projects/{hello_project}/files/sources/{source_name}.csv")
    assert stored.json()["content"] == ""
