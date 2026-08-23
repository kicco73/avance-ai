"""GET/PUT /api/projects/{project_name}/project — reads/writes the optional
top-level `project:` section (id/ui-label/ui-description) of index.yml
(ProjectService.get_project_metadata/set_project_field).
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.contract


BARE_YML = "init-action:\n  target: a\nstates:\n  a:\n    contextual-prompt: hi\n"


class TestGetProjectMetadata:
    def test_reports_none_for_every_field_when_no_project_section_is_declared(self, client):
        response = client.put(
            "/api/projects/bare", content=BARE_YML.encode(), headers={"Content-Type": "application/x-yaml"}
        )
        assert response.status_code == 200, response.text

        response = client.get("/api/projects/bare/project")
        assert response.status_code == 200
        assert response.json()["project"] == {
            "id": None, "ui_label": None, "ui_description": None,
            "talk_enabled": True, "signal_tracking_on_ai_message": False,
        }

    def test_reports_declared_fields(self, client, hello_project):
        client.put(f"/api/projects/{hello_project}/project/id", json={"value": "concierge"})
        client.put(f"/api/projects/{hello_project}/project/ui-label", json={"value": "Concierge"})
        client.put(f"/api/projects/{hello_project}/project/ui-description", json={"value": "The front desk."})

        response = client.get(f"/api/projects/{hello_project}/project")
        assert response.status_code == 200
        assert response.json()["project"] == {
            "id": "concierge", "ui_label": "Concierge", "ui_description": "The front desk.",
            "talk_enabled": True, "signal_tracking_on_ai_message": False,
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

    def test_rejects_an_invalid_identifier(self, client, hello_project):
        response = client.put(f"/api/projects/{hello_project}/project/id", json={"value": "not a valid id"})
        assert response.status_code == 400

    def test_rejects_an_id_already_claimed_by_another_project(self, client, hello_project):
        client.put("/api/projects/other", content=b"init-action:\n  target: a\nstates:\n  a:\n    contextual-prompt: hi\n"
                    b"project:\n  id: taken\n", headers={"Content-Type": "application/x-yaml"})
        response = client.put(f"/api/projects/{hello_project}/project/id", json={"value": "taken"})
        assert response.status_code == 400

    def test_clearing_the_id_removes_it_rather_than_writing_an_empty_string(self, client, hello_project):
        client.put(f"/api/projects/{hello_project}/project/id", json={"value": "hello_id"})

        response = client.put(f"/api/projects/{hello_project}/project/id", json={"value": ""})
        assert response.status_code == 200
        assert response.json()["id"] is None

        # A different project may now freely claim the id "hello_id" just freed.
        response = client.put("/api/projects/another", content=b"init-action:\n  target: a\nstates:\n  a:\n    contextual-prompt: hi\n"
                               b"project:\n  id: hello_id\n", headers={"Content-Type": "application/x-yaml"})
        assert response.status_code == 200, response.text

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
