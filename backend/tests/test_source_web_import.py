"""Tests for the Source card's own "AI Web Import" — POST
/api/projects/{id}/sources/{name}/web-import, its 4-step WebImportJob
(crawl, schema extraction, CSV extraction, import) and the SSE progress
the same response streams back, ending with the CSV written into the
source's own archive exactly as a manual upload would leave it."""
from __future__ import annotations

import asyncio
import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from project.web_import_crawler import CrawledPage, _resolve_result_url
from project.web_import_job import WebImportJob

pytestmark = pytest.mark.contract

PAGES = [
    CrawledPage(url="https://example.com/a", title="Dentists", text="Dr. Nuria — Eixample — 4.8"),
    CrawledPage(url="https://example.com/b", title="More dentists", text="Dr. Pau — Gracia — 4.6"),
]
COLUMNS = ["name", "district", "rating"]
MODEL_CSV = "name,district,rating\nDr. Nuria,Eixample,4.8\nDr. Pau,Gracia,4.6\n"
# Longer than QueueProgressBroadcaster's own 100ms batching window, so
# each step lands as its own SSE chunk instead of being coalesced.
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
    project_service._web_crawler = crawler
    project_service._ai_service = ai_service
    return crawler, ai_service


def _add_source(client: TestClient, project_id: str, name_hint: str = "places") -> str:
    response = client.post(f"/api/projects/{project_id}/sources?file_name={name_hint}", content=b"")
    assert response.status_code == 200, response.text
    return response.json()["name"]


def _web_import(client: TestClient, project_id: str, source_name: str, query: str):
    return client.post(f"/api/projects/{project_id}/sources/{source_name}/web-import", json={"query": query})


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
    # One quarter per completed step, nothing in between — a step
    # finishing inside QueueProgressBroadcaster's own batching window is
    # coalesced into the next chunk, so the sequence is a growing prefix
    # of the five, never a percentage of some other shape.
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

    stored = client.get(f"/api/projects/{hello_project}/files/sources/{source_name}.csv")
    assert stored.status_code == 200, stored.text
    assert stored.json()["content"] == MODEL_CSV


def test_web_import_normalizes_the_model_csv_against_the_extracted_schema(app, client, hello_project):
    _install_fakes(app, csv_text="name,district,rating\nDr. Nuria,Eixample,4.8,ignored\nDr. Pau\n")
    source_name = _add_source(client, hello_project)

    response = _web_import(client, hello_project, source_name, "dentists")

    assert response.status_code == 200, response.text
    stored = client.get(f"/api/projects/{hello_project}/files/sources/{source_name}.csv")
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
    stored = client.get(f"/api/projects/{hello_project}/files/sources/{source_name}.csv")
    assert stored.json()["content"] == ""


def test_normalize_csv_forces_the_schema_header_and_squares_every_row_off_against_it():
    normalized = WebImportJob._normalize_csv(
        "```csv\na,b\n1,2,3\n\n4\n```", ["a", "b"],
    )
    assert normalized == "a,b\n1,2\n4,\n"


def test_parse_columns_reads_a_fenced_or_prefixed_json_array_and_refuses_anything_else():
    assert WebImportJob._parse_columns('```json\n["a", " b "]\n```') == ["a", "b"]
    assert WebImportJob._parse_columns('Here you go: ["a"]') == ["a"]
    for bad in ("no array here", "[]", "[\"\"]"):
        with pytest.raises(ValueError):
            WebImportJob._parse_columns(bad)


def test_a_search_result_link_resolves_to_its_real_target_and_never_to_the_engine_itself():
    assert _resolve_result_url("//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2Fa&rut=x") == "https://example.com/a"
    assert _resolve_result_url("https://example.com/b") == "https://example.com/b"
    assert _resolve_result_url("https://duckduckgo.com/settings") is None
    assert _resolve_result_url("/about") is None
