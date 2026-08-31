"""POST /api/projects/{project_name}/invites (ShareProjectDialog.vue's own
trigger — a fresh Invite row every time the dialog opens) and
GET /api/projects/by-invite/{code} (the existence-only resolution an
already-authenticated landing needs — see ProjectService.
get_project_name_by_invite_code). Registration-time validation
(expiry/max-shares) lives in test_auth_service.py instead, against
AuthService.complete_registration directly.
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.contract


class TestPostCreateInvite:
    def test_creates_an_invite_with_a_6_character_code_and_the_configured_defaults(self, client, hello_project):
        response = client.post(f"/api/projects/{hello_project}/invites")
        assert response.status_code == 200, response.text
        body = response.json()
        assert len(body["code"]) == 6
        assert body["code"].isalnum()
        assert body["max_shares"] == 3  # ProjectService's own default
        assert body["expires_at"]

    def test_every_call_creates_a_fresh_invite_with_its_own_code(self, client, hello_project):
        first = client.post(f"/api/projects/{hello_project}/invites").json()
        second = client.post(f"/api/projects/{hello_project}/invites").json()
        assert first["code"] != second["code"]

    def test_unknown_project_is_404(self, client):
        response = client.post("/api/projects/does-not-exist/invites")
        assert response.status_code == 404


class TestGetProjectByInviteCode:
    def test_resolves_a_generated_code_to_its_project_name(self, client, hello_project):
        code = client.post(f"/api/projects/{hello_project}/invites").json()["code"]

        response = client.get(f"/api/projects/by-invite/{code}")
        assert response.status_code == 200
        assert response.json() == {"project_name": hello_project}

    def test_an_unresolved_code_reports_none_rather_than_404ing(self, client):
        response = client.get("/api/projects/by-invite/NOSUCH")
        assert response.status_code == 200
        assert response.json() == {"project_name": None}
