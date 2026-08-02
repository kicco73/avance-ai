"""GET /api/projects/{name}/graph — see ProjectService.get_project_graph.
Covers the one new thing added for the Inspector graph's own initial-state
arrow: an edge with source="" (the automaton's own init_action, never
attached to any real state) alongside every state's real outgoing edges.
"""
from __future__ import annotations


def test_graph_excludes_the_reserved_implicit_state_from_nodes(client, hello_project):
    graph = client.get("/api/projects/hello/graph").json()

    assert [n["key"] for n in graph["nodes"]] == ["Hello"]
    assert graph["nodes"][0]["is_start"] is True


def test_graph_includes_an_edge_from_the_reserved_state_for_init_action(client, hello_project):
    """The one edge with no real source state — the automaton's own
    "arrow from nowhere" into its start state (see InspectorGraphTab.vue's
    transparent pseudo-node, and Signals.old_state's own "" convention for
    the same "no real prior state" idea elsewhere)."""
    graph = client.get("/api/projects/hello/graph").json()

    init_edges = [e for e in graph["edges"] if e["source"] == ""]
    assert len(init_edges) == 1
    assert init_edges[0]["target"] == "Hello"
    assert init_edges[0]["action_name"] == "init_action"
    assert init_edges[0]["has_trigger"] is False
