"""GET /api/projects/{name}/graph — see ProjectService.get_project_graph.
Covers the one new thing added for the Inspector graph's own initial-state
arrow: an edge with source="" (the automaton's own init_action, never
attached to any real state) alongside every state's real outgoing edges.
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.contract


def test_graph_excludes_the_reserved_implicit_state_from_nodes(client, hello_project):
    graph = client.get("/api/projects/hello/graph").json()

    assert [n["key"] for n in graph["nodes"]] == ["Hello"]
    assert graph["nodes"][0]["is_start"] is True


def test_graph_includes_an_edge_from_the_reserved_state_for_init_action(client, hello_project):
    """The one edge with no real source state — the automaton's own
    "arrow from nowhere" into its start state (see InspectorGraphTab.vue's
    transparent pseudo-node, and Tracking.old_state's own "" convention for
    the same "no real prior state" idea elsewhere)."""
    graph = client.get("/api/projects/hello/graph").json()

    init_edges = [e for e in graph["edges"] if e.get("source") in ("", None)]
    assert len(init_edges) == 1
    assert init_edges[0]["target"] == "Hello"
    assert init_edges[0]["action_name"] in {"init_action", "init-action"}
    assert init_edges[0]["has_trigger"] is False


def test_on_enter_is_reported_per_edge_not_per_node(client):
    """on-enter belongs to the action (edge), not its destination state
    (node) — see automaton.Action.on_enter."""
    yml = (
        "init-action:\n  target: a\n"
        "states:\n"
        "  a:\n"
        "    contextual-prompt: hi\n"
        "    actions:\n"
        "      - name: go-quiet\n"
        "        target: b\n"
        "      - name: go-loud\n"
        "        target: b\n"
        "        on-enter: celebrate\n"
        "  b:\n"
        "    contextual-prompt: there\n"
    )
    resp = client.put("/api/projects/on-enter-proj", content=yml.encode(), headers={"Content-Type": "application/x-yaml"})
    assert resp.status_code == 200, resp.text

    graph = client.get("/api/projects/on-enter-proj/graph").json()

    assert "on-enter" not in graph["nodes"][0]
    edges_by_name = {e["action_name"]: e for e in graph["edges"]}
    assert edges_by_name["go-quiet"]["on-enter"] is None
    assert edges_by_name["go-loud"]["on-enter"] == "celebrate"
