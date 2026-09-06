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


def test_graph_reports_the_builders_own_warnings(client):
    # ai-may-write-sources on an avance:env source the state never reads
    # is a real AutomatonBuilder warning (see automaton_builder.py), never
    # a build error — same one the design view's own yellow banner surfaces.
    yml = (
        "project:\n  id: warn_proj\n"
        "env:\n  pnr:\n    ai-access: readwrite\n    ai-definition: The record locator.\n"
        "sources:\n  env:\n    url: avance:env\n    ai-definition: The automaton's variables.\n"
        "init-action:\n  target: a\n"
        "states:\n  a:\n    contextual-prompt: hi\n    ai-may-write-sources: [env]\n"
    )
    resp = client.post("/api/projects/upload", content=yml.encode(), headers={"Content-Type": "application/x-yaml"})
    assert resp.status_code == 200, resp.text
    project_id = parse_sse_result(resp)["project_id"]

    graph = client.get(f"/api/projects/{project_id}/graph").json()

    assert len(graph["build_warnings"]) == 1
    assert "never sees the current values" in graph["build_warnings"][0]


def test_graph_includes_an_edge_from_the_reserved_state_for_init_action(client, hello_project):
    """The init-action edge has no real source state, unlike edges
    between two real states."""
    graph = client.get(f"/api/projects/{hello_project}/graph").json()

    init_edges = [e for e in graph["edges"] if e.get("source") in ("", None)]
    assert len(init_edges) == 1
    assert init_edges[0]["action"]["target"] == "Hello"
    assert init_edges[0]["action"]["name"] in {"init_action", "init-action"}
    assert init_edges[0]["action"]["has_trigger"] is False


def test_on_enter_is_reported_per_edge_not_per_node(client):
    """on-enter belongs to the action (edge), not its destination state
    (node) — see automaton.Action.on_enter."""
    yml = (
        "project:\n  id: on_enter_proj\n"
        "init-action:\n  target: a\n"
        "states:\n"
        "  a:\n"
        "    contextual-prompt: hi\n"
        "    actions:\n"
        "      - name: go-quiet\n"
        "        target: b\n"
        "      - name: go-loud\n"
        "        target: b\n"
        "        on-enter: actuator.celebrate()\n"
        "  b:\n"
        "    contextual-prompt: there\n"
    )
    resp = client.post("/api/projects/upload", content=yml.encode(), headers={"Content-Type": "application/x-yaml"})
    assert resp.status_code == 200, resp.text
    project_id = parse_sse_result(resp)["project_id"]

    graph = client.get(f"/api/projects/{project_id}/graph").json()

    assert "on-enter" not in graph["nodes"][0]["state"]
    edges_by_name = {e["action"]["name"]: e for e in graph["edges"]}
    assert edges_by_name["go-quiet"]["action"]["on-enter"] is None
    assert edges_by_name["go-loud"]["action"]["on-enter"] == "actuator.celebrate()"
