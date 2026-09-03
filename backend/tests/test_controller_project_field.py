"""GET/PUT /api/projects/{project_id}/project — reads/writes the
top-level `project:` section (id/family/ui-label/ui-description) of
index.yml (ProjectService.get_project_metadata/set_project_field). See
test_controller_invites.py for the "share project" invite endpoints.
"""
from __future__ import annotations

import pytest

from conftest import parse_sse_result

pytestmark = pytest.mark.contract


BARE_YML = "init-action:\n  target: a\nstates:\n  a:\n    contextual-prompt: hi\n"


class TestGetProjectMetadata:
    def test_reports_defaults_for_every_field_the_project_never_declared(self, client):
        response = client.post(
            "/api/projects/upload", content=("project:\n  id: bare\n" + BARE_YML).encode(),
            headers={"Content-Type": "application/x-yaml"},
        )
        assert response.status_code == 200, response.text
        project_id = parse_sse_result(response)["project_id"]

        response = client.get(f"/api/projects/{project_id}/project")
        assert response.status_code == 200
        assert response.json()["project"] == {
            "id": "bare", "family": None, "revision": 0, "ui_label": None, "ui_description": None,
            "talk_enabled": True, "signal_tracking_on_ai_message": False, "general_prompt": "",
        }

    def test_reports_declared_fields(self, client, hello_project):
        # Editing "id" is a real rename — the response's own "id" is the
        # project's new identity, and every call afterward must use it.
        response = client.put(f"/api/projects/{hello_project}/project/id", json={"value": "concierge"})
        project_id = response.json()["id"]
        client.put(f"/api/projects/{project_id}/project/ui-label", json={"value": "Concierge"})
        client.put(f"/api/projects/{project_id}/project/ui-description", json={"value": "The front desk."})

        response = client.get(f"/api/projects/{project_id}/project")
        assert response.status_code == 200
        assert response.json()["project"] == {
            "id": "concierge", "family": None, "revision": 0, "ui_label": "Concierge", "ui_description": "The front desk.",
            "talk_enabled": True, "signal_tracking_on_ai_message": False, "general_prompt": "",
        }

    def test_unknown_project_is_404(self, client):
        response = client.get("/api/projects/does-not-exist/project")
        assert response.status_code == 404


class TestPutProjectField:
    def test_edits_id(self, client, hello_project):
        response = client.put(f"/api/projects/{hello_project}/project/id", json={"value": "hello_id"})
        assert response.status_code == 200
        assert response.json()["id"] == "hello_id"

    def test_edits_ui_label(self, client, hello_project):
        response = client.put(f"/api/projects/{hello_project}/project/ui-label", json={"value": "Hello World"})
        assert response.status_code == 200
        assert response.json()["ui_label"] == "Hello World"

    def test_edits_ui_description(self, client, hello_project):
        response = client.put(f"/api/projects/{hello_project}/project/ui-description", json={"value": "A greeting bot."})
        assert response.status_code == 200
        assert response.json()["ui_description"] == "A greeting bot."

    def test_edits_talk_enabled(self, client, hello_project):
        response = client.put(f"/api/projects/{hello_project}/project/talk-enabled", json={"value": False})
        assert response.status_code == 200
        assert response.json()["talk_enabled"] is False

    def test_edits_signal_tracking_on_ai_message(self, client, hello_project):
        response = client.put(
            f"/api/projects/{hello_project}/project/signal-tracking-on-ai-message", json={"value": True}
        )
        assert response.status_code == 200
        assert response.json()["signal_tracking_on_ai_message"] is True

    def test_edits_general_prompt(self, client, hello_project):
        response = client.put(
            f"/api/projects/{hello_project}/project/general-prompt", json={"value": "Always be polite."}
        )
        assert response.status_code == 200
        assert response.json()["general_prompt"] == "Always be polite."
        assert client.get(f"/api/projects/{hello_project}/project").json()["project"]["general_prompt"] == "Always be polite."

    def test_general_prompt_is_stored_at_the_top_level_not_under_project(self, client, hello_project):
        client.put(f"/api/projects/{hello_project}/project/general-prompt", json={"value": "Always be polite."})

        # general-prompt must sit as a sibling of 'project:', never nested
        # inside it (see AutomatonYamlEditor.set_project_field).
        response = client.get(f"/api/projects/{hello_project}/files/index.yml")
        assert "general-prompt: Always be polite." in response.json()["content"]

    def test_clearing_general_prompt_removes_it_rather_than_writing_an_empty_string(self, client, hello_project):
        client.put(f"/api/projects/{hello_project}/project/general-prompt", json={"value": "Always be polite."})

        response = client.put(f"/api/projects/{hello_project}/project/general-prompt", json={"value": ""})
        assert response.status_code == 200
        assert response.json()["general_prompt"] == ""

    def test_rejects_an_invalid_identifier(self, client, hello_project):
        response = client.put(f"/api/projects/{hello_project}/project/id", json={"value": "not a valid id"})
        assert response.status_code == 400

    def test_rejects_an_id_already_claimed_by_another_project(self, client, hello_project):
        client.post(
            "/api/projects/upload",
            content=b"project:\n  id: taken\ninit-action:\n  target: a\nstates:\n  a:\n    contextual-prompt: hi\n",
            headers={"Content-Type": "application/x-yaml"},
        )
        response = client.put(f"/api/projects/{hello_project}/project/id", json={"value": "taken"})
        assert response.status_code == 400

    def test_clearing_the_id_is_rejected_since_id_is_now_mandatory(self, client, hello_project):
        """project.id is a required YAML field now (AutomatonBuilder.build
        rejects a missing/empty one outright) — unlike the old optional
        metadata field, there's no "no id" state left to fall back to, so
        the whole edit is rejected rather than silently clearing the key."""
        response = client.put(f"/api/projects/{hello_project}/project/id", json={"value": ""})
        assert response.status_code == 400

        # Rejected — the project keeps its original id.
        response = client.get(f"/api/projects/{hello_project}/project")
        assert response.status_code == 200
        assert response.json()["project"]["id"] == hello_project

    def test_rejects_a_field_not_on_the_whitelist(self, client, hello_project):
        response = client.put(f"/api/projects/{hello_project}/project/name", json={"value": "x"})
        assert response.status_code == 400

    def test_unknown_project_is_404(self, client):
        response = client.put("/api/projects/does-not-exist/project/ui-label", json={"value": "x"})
        assert response.status_code == 404

    def test_trims_leading_and_trailing_whitespace(self, client, hello_project):
        response = client.put(f"/api/projects/{hello_project}/project/ui-label", json={"value": "  Hello World  "})
        assert response.status_code == 200
        assert response.json()["ui_label"] == "Hello World"
