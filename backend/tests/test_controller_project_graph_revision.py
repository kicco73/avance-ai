"""GET .../graph, .../signals, .../env-keys all gained an optional
`session_id` query param (see ProjectService._resolve_inspector_revision) —
omitted, they read the current draft exactly as before; given, they pin to
the exact revision that session's own automaton actually ran against, the
same live/native-vs-'test' distinction GET .../files/{file_name}/content's
own session_id already follows (see test_controller_project_file_content.
py's own TestGetProjectFileContent, the pattern this mirrors). This is what
lets LabelProjectView.vue's own Inspect panel show an older session's real
structure instead of today's, once the project has moved on since.
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.contract

TWO_STATE_YML = (
    "init-action:\n  target: a\n"
    "states:\n"
    "  a:\n"
    "    contextual-prompt: hi\n"
    "    actions:\n"
    "      - name: go\n"
    "        target: b\n"
    "  b:\n"
    "    contextual-prompt: there\n"
)

THREE_STATE_YML = (
    "init-action:\n  target: a\n"
    "states:\n"
    "  a:\n"
    "    contextual-prompt: hi\n"
    "    actions:\n"
    "      - name: go\n"
    "        target: b\n"
    "  b:\n"
    "    contextual-prompt: there\n"
    "  c:\n"
    "    contextual-prompt: new\n"
)


def _setup_pinned_session(client) -> int:
    """A live session pinned to revision 0 (a two-state project) — firing
    a real action first, same reasoning as test_controller_project_file_
    content.py's own identically-named helper's own docstring (establishes
    current_state reliably before the later edit/publish below)."""
    client.put("/api/projects/proj", content=TWO_STATE_YML.encode(), headers={"Content-Type": "application/x-yaml"})
    client.post("/api/projects/proj/publish", json={})

    session_response = client.get("/api/chat/session")
    assert session_response.status_code == 200, session_response.text
    session_id = session_response.json()["id"]
    action_response = client.post("/api/action", json={"action_name": "go", "session_id": session_id})
    assert action_response.status_code == 200, action_response.text
    return session_id


class TestGetProjectGraphRevision:
    def test_without_session_id_reflects_the_current_draft(self, client):
        session_id = _setup_pinned_session(client)
        client.put("/api/projects/proj", content=THREE_STATE_YML.encode(), headers={"Content-Type": "application/x-yaml"})
        client.post("/api/projects/proj/publish", json={})

        response = client.get("/api/projects/proj/graph")

        assert response.status_code == 200
        assert {n["state"]["key"] for n in response.json()["nodes"]} == {"a", "b", "c"}

    def test_a_live_session_stays_pinned_to_its_own_published_revision(self, client):
        session_id = _setup_pinned_session(client)
        client.put("/api/projects/proj", content=THREE_STATE_YML.encode(), headers={"Content-Type": "application/x-yaml"})
        client.post("/api/projects/proj/publish", json={})

        response = client.get(f"/api/projects/proj/graph?session_id={session_id}")

        assert response.status_code == 200
        assert {n["state"]["key"] for n in response.json()["nodes"]} == {"a", "b"}

    def test_a_test_session_always_tracks_the_live_draft(self, client):
        client.put("/api/projects/proj", content=TWO_STATE_YML.encode(), headers={"Content-Type": "application/x-yaml"})
        client.post("/api/projects/proj/publish", json={})

        test_session_response = client.post("/api/projects/proj/test-sessions")
        assert test_session_response.status_code == 200, test_session_response.text
        test_session_id = test_session_response.json()["id"]
        # Establishes current_state reliably first — same reasoning as
        # _setup_pinned_session's own live-session equivalent: a session
        # with no real action fired yet is wiped by _finalize_project_
        # update's own "current_state can't be determined" cleanup, the
        # next time the active project is edited.
        action_response = client.post("/api/action", json={"action_name": "go", "session_id": test_session_id})
        assert action_response.status_code == 200, action_response.text

        client.put("/api/projects/proj", content=THREE_STATE_YML.encode(), headers={"Content-Type": "application/x-yaml"})

        response = client.get(f"/api/projects/proj/graph?session_id={test_session_id}")

        assert response.status_code == 200, response.text
        assert {n["state"]["key"] for n in response.json()["nodes"]} == {"a", "b", "c"}

    def test_unknown_session_id_is_404(self, client):
        client.put("/api/projects/proj", content=TWO_STATE_YML.encode(), headers={"Content-Type": "application/x-yaml"})

        response = client.get("/api/projects/proj/graph?session_id=999999")

        assert response.status_code == 404


class TestGetProjectSignalsRevision:
    def test_a_live_session_stays_pinned_to_its_own_published_revision(self, client):
        client.put("/api/projects/proj", content=TWO_STATE_YML.encode(), headers={"Content-Type": "application/x-yaml"})
        client.post("/api/projects/proj/publish", json={})
        session_response = client.get("/api/chat/session")
        session_id = session_response.json()["id"]
        client.post("/api/action", json={"action_name": "go", "session_id": session_id})

        client.put(
            "/api/projects/proj",
            content=(TWO_STATE_YML + "signals:\n  mood:\n    definition: \"1\"\n").encode(),
            headers={"Content-Type": "application/x-yaml"},
        )
        client.post("/api/projects/proj/publish", json={})

        pinned = client.get(f"/api/projects/proj/signals?session_id={session_id}")
        current = client.get("/api/projects/proj/signals")

        assert pinned.json()["signals"] == []
        assert [s["signal"]["name"] for s in current.json()["signals"]] == ["mood"]


class TestGetProjectEnvKeysRevision:
    def test_a_live_session_stays_pinned_to_its_own_published_revision(self, client):
        client.put("/api/projects/proj", content=TWO_STATE_YML.encode(), headers={"Content-Type": "application/x-yaml"})
        client.post("/api/projects/proj/publish", json={})
        session_response = client.get("/api/chat/session")
        session_id = session_response.json()["id"]
        client.post("/api/action", json={"action_name": "go", "session_id": session_id})

        client.put(
            "/api/projects/proj",
            content=(TWO_STATE_YML + "env:\n  greeting:\n    value: \"'hi'\"\n").encode(),
            headers={"Content-Type": "application/x-yaml"},
        )
        client.post("/api/projects/proj/publish", json={})

        pinned = client.get(f"/api/projects/proj/env-keys?session_id={session_id}")
        current = client.get("/api/projects/proj/env-keys")

        assert pinned.json()["env_keys"] == []
        assert [e["env_key"]["name"] for e in current.json()["env_keys"]] == ["greeting"]
