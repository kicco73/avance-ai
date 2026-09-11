"""GET /api/projects/{project_id}/graph (ProjectService.get_project_graph). Includes
an edge with source="" for the automaton's init_action alongside each
state's real outgoing edges.
"""
from __future__ import annotations

import pytest

from conftest import parse_sse_result

pytestmark = pytest.mark.contract


def test_graph_excludes_the_reserved_implicit_state_from_nodes(client, hello_project):
    graph = client.get(f"/api/projects/{hello_project}/graph").json()

    assert [n["state"]["key"] for n in graph["nodes"]] == ["Hello"]
    assert graph["nodes"][0]["is_start"] is True


def test_graph_reports_no_build_warnings_for_a_clean_project(client, hello_project):
    graph = client.get(f"/api/projects/{hello_project}/graph").json()

    assert graph["build_warnings"] == []


def test_graph_includes_an_edge_from_the_reserved_state_for_init_action(client, hello_project):
    """The init-action edge has no real source state, unlike edges
    between two real states."""
    graph = client.get(f"/api/projects/{hello_project}/graph").json()

    init_edges = [e for e in graph["edges"] if e.get("source") in ("", None)]
    assert len(init_edges) == 1
    assert init_edges[0]["action"]["target"] == "Hello"
    assert init_edges[0]["action"]["name"] in {"init_action", "init-action"}
    assert init_edges[0]["action"]["has_trigger"] is False


def test_task_is_reported_per_edge_not_per_node(client):
    """task belongs to the action (edge), not its destination state
    (node) — see automaton.Action.task."""
    yml = (
        "project:\n  id: task_proj\n"
        "init-action:\n  target: a\n"
        "states:\n"
        "  a:\n"
        "    contextual-prompt: hi\n"
        "    actions:\n"
        "      - name: go-quiet\n"
        "        target: b\n"
        "      - name: go-loud\n"
        "        target: b\n"
        "        task: task.send_mail(user.email, 'hi')\n"
        "  b:\n"
        "    contextual-prompt: there\n"
    )
    resp = client.post("/api/projects/upload", content=yml.encode(), headers={"Content-Type": "application/x-yaml"})
    assert resp.status_code == 200, resp.text
    project_id = parse_sse_result(resp)["project_id"]

    graph = client.get(f"/api/projects/{project_id}/graph").json()

    assert "task" not in graph["nodes"][0]["state"]
    edges_by_name = {e["action"]["name"]: e for e in graph["edges"]}
    assert edges_by_name["go-quiet"]["action"]["task"] is None
    assert edges_by_name["go-loud"]["action"]["task"] == "task.send_mail(user.email, 'hi')"
