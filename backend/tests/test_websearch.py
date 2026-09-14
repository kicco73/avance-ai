"""WebSearch — a query in, CSV out (see src/websearch/). What it answers
with is decided twice by a model: which columns describe the pages it
crawled, then what each row holds. Both replies arrive as free text, so
what is checked here is that a reply the model dressed up (a fence, a
preamble, a ragged row) still becomes the table the caller was promised,
and that an unusable one is refused rather than silently emptied."""
from __future__ import annotations

import json

import pytest

from automaton.automaton import Action, Automaton, Source, State
from metrics.metric_service import MetricService
from system.web_session import WebSession
from tracking.actuators.actuator_set import FakeTaskNamespace, LiveTaskNamespace
from tracking.env import Env
from tracking.evaluation_scope import EvaluationScopeBuilder
from tracking.fixed_project_context import FixedProjectContext
from tracking.session_facts import SessionFacts
from tracking.user_facts import UserFacts
from websearch import CrawledPage, WebSearch, resolve_result_url

pytestmark = pytest.mark.contract

PAGES = [
    CrawledPage(url="https://example.com/a", title="Dentists", text="Dr. Nuria — Eixample — 4.8"),
    CrawledPage(url="https://example.com/b", title="More dentists", text="Dr. Pau — Gracia — 4.6"),
]
COLUMNS = ["name", "district", "rating"]
MODEL_CSV = "name,district,rating\nDr. Nuria,Eixample,4.8\nDr. Pau,Gracia,4.6\n"


class FakeCrawler:

    def __init__(self, pages: list[CrawledPage]) -> None:
        self.pages = pages
        self.queries: list[str] = []

    async def crawl(self, query: str) -> list[CrawledPage]:
        self.queries.append(query)
        return self.pages


class FakeWebSearchAi:

    def __init__(self, columns: list[str], csv_text: str) -> None:
        self.columns = columns
        self.csv_text = csv_text
        self.prompts: list[str] = []

    async def prompt(self, prompt: str, channels=None, tool_set=None):
        self.prompts.append(prompt)
        if "JSON array" in prompt:
            return f"```json\n{json.dumps(self.columns)}\n```"
        return self.csv_text


async def test_a_query_comes_back_as_csv_over_the_schema_the_crawled_pages_suggested():
    crawler = FakeCrawler(PAGES)
    ai_service = FakeWebSearchAi(COLUMNS, MODEL_CSV)

    csv_text = await WebSearch(ai_service, crawler).csv_for("dentists in Barcelona")

    assert csv_text == MODEL_CSV
    assert crawler.queries == ["dentists in Barcelona"]
    assert len(ai_service.prompts) == 2
    assert all("dentists in Barcelona" in prompt for prompt in ai_service.prompts)
    assert "Dr. Nuria — Eixample — 4.8" in ai_service.prompts[0]


async def test_the_csv_is_normalized_against_the_schema_whatever_shape_the_model_replied_in():
    ai_service = FakeWebSearchAi(
        COLUMNS, "```csv\nname,district,rating\nDr. Nuria,Eixample,4.8,ignored\n\nDr. Pau\n```",
    )

    csv_text = await WebSearch(ai_service, FakeCrawler(PAGES)).csv_for("dentists")

    assert csv_text == "name,district,rating\nDr. Nuria,Eixample,4.8\nDr. Pau,,\n"


async def test_a_schema_the_model_did_not_return_fails_the_search_instead_of_inventing_one():
    with pytest.raises(ValueError):
        await WebSearch(FakeWebSearchAi([], MODEL_CSV), FakeCrawler(PAGES)).csv_for("dentists")


def test_parse_columns_reads_a_fenced_or_prefixed_json_array_and_refuses_anything_else():
    assert WebSearch.parse_columns('```json\n["a", " b "]\n```') == ["a", "b"]
    assert WebSearch.parse_columns('Here you go: ["a"]') == ["a"]
    for bad in ("no array here", "[]", "[\"\"]"):
        with pytest.raises(ValueError):
            WebSearch.parse_columns(bad)


def test_a_search_result_link_resolves_to_its_real_target_and_never_to_the_engine_itself():
    assert resolve_result_url("//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2Fa&rut=x") == "https://example.com/a"
    assert resolve_result_url("https://example.com/b") == "https://example.com/b"
    assert resolve_result_url("https://duckduckgo.com/settings") is None
    assert resolve_result_url("/about") is None


def test_task_websearch_hands_the_script_the_csv_and_runs_suppressed_or_not():
    """`task.websearch` reads the public web and returns text — nothing
    a test session's "Run actuators" toggle has anything to suppress, so
    both namespaces answer the same way `prompt` does."""
    for namespace in (
        FakeTaskNamespace(crawler=FakeCrawler(PAGES)),
        LiveTaskNamespace(dispatcher=None, crawler=FakeCrawler(PAGES)),
    ):
        bound = namespace.with_ai_service(FakeWebSearchAi(COLUMNS, MODEL_CSV))

        assert bound.websearch("dentists in Barcelona") == MODEL_CSV


def test_task_websearch_returns_empty_string_with_no_ai_service_bound():
    assert FakeTaskNamespace(crawler=FakeCrawler(PAGES)).websearch("dentists") == ""


PROJECT_ID = "proj"
WEB_SOURCE = Source(name="web", url="websearch:user", ui_label="Web")


def _automaton(db) -> Automaton:
    db.ensure_project(PROJECT_ID)
    init_action = Action(name="init_action", ui_label="init_action", ui_button="", target="a")
    action = Action(name="advance", ui_label="Advance", ui_button="Advance", target="a")
    automaton = Automaton(
        init_action=init_action,
        states={
            "": State(key="", ui_label="", final=False, actions=[init_action]),
            "a": State(key="a", ui_label="A", final=False, contextual_prompt="hi", actions=[action]),
        },
        general_prompt="", signals=[], general_attachments={},
        autotracking_on_ai_message=False, project_id=PROJECT_ID, sources=[WEB_SOURCE],
    )
    automaton.set_storage_location(db.get_project_revision(PROJECT_ID))
    return automaton


def _scope(db, automaton: Automaton):
    context = FixedProjectContext(automaton=automaton, project_id=PROJECT_ID)
    builder = EvaluationScopeBuilder(
        Env(), MetricService(db, context), SessionFacts(db, context), UserFacts(db), db,
        task_namespace=FakeTaskNamespace(crawler=FakeCrawler(PAGES)),
    )
    return builder.build(automaton, "a", {})


def test_what_task_websearch_found_is_kept_for_this_user_and_read_back_through_a_websearch_source(db):
    automaton = _automaton(db)
    scope = _scope(db, automaton)

    found = scope["task"].with_ai_service(FakeWebSearchAi(COLUMNS, MODEL_CSV)).websearch("dentists in Barcelona")

    assert found == MODEL_CSV
    assert db.get_archive(PROJECT_ID, "cache/websearch/proj/user", revision=automaton.revision) == MODEL_CSV.encode()
    assert scope["source"].web.select_rows_containing("Gracia") == "name,district,rating\nDr. Pau,Gracia,4.6\n"
    assert scope["source"].web.value("Gracia", key="rating") == "4.6"


def test_a_websearch_source_reads_empty_before_any_search_and_never_another_users_results(db):
    scope = _scope(db, _automaton(db))

    assert scope["source"].web.select_rows_containing("Gracia") == ""
    assert scope["source"].web.value("Gracia", key="rating") == ""

    scope["task"].with_ai_service(FakeWebSearchAi(COLUMNS, MODEL_CSV)).websearch("dentists in Barcelona")
    assert scope["source"].web.select_rows_containing("Gracia") != ""
    with WebSession().impersonate("somebody-else"):
        assert scope["source"].web.select_rows_containing("Gracia") == ""
