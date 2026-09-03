"""POST /api/projects/{project_name}/invites (ShareProjectDialog.vue's own
trigger — a fresh Invite row every time the dialog opens) and
POST /api/projects/by-invite/{code} (the resolution an already-
authenticated landing needs — see ProjectService.resolve_invite_link).
The default test session role (supervisor, see conftest.py's
_default_session_user) isn't gated by UserProject at all, so most cases
below stay existence-only; TestPostResolveInviteCodeAsUser covers the
role='user' grant/budget behavior specifically. Registration-time
validation (expiry/max-shares) lives in test_auth_service.py instead,
against AuthService.complete_registration directly.
"""
from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from session import Session

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


class TestPostResolveInviteCode:
    def test_resolves_a_generated_code_to_its_project_name(self, client, hello_project):
        code = client.post(f"/api/projects/{hello_project}/invites").json()["code"]

        response = client.post(f"/api/projects/by-invite/{code}")
        assert response.status_code == 200
        assert response.json() == {"project_id": hello_project}

    def test_an_unresolved_code_reports_none_rather_than_404ing(self, client):
        response = client.post("/api/projects/by-invite/NOSUCH")
        assert response.status_code == 200
        assert response.json() == {"project_id": None}


class TestPostResolveInviteCodeAsUser:
    """The default test session role (supervisor) isn't gated by
    UserProject, so these override Session().role directly — same
    pattern as test_controller_chat_truncate.py's own role downgrade."""

    def test_grants_access_and_returns_the_project_name(self, app_db, client, hello_project):
        code = client.post(f"/api/projects/{hello_project}/invites").json()["code"]
        Session().role = "user"

        response = client.post(f"/api/projects/by-invite/{code}")

        assert response.status_code == 200
        assert response.json() == {"project_id": hello_project}
        assert app_db.user_has_project_access(Session().user, hello_project) is True

    def test_an_expired_link_is_forbidden_with_no_existing_access(self, app_db, client, hello_project):
        app_db.create_invite("EXPIR1", hello_project, None, datetime.utcnow() - timedelta(days=1), max_shares=3)
        Session().role = "user"

        response = client.post("/api/projects/by-invite/EXPIR1")

        assert response.status_code == 403
        assert app_db.user_has_project_access(Session().user, hello_project) is False

    def test_revisiting_a_project_already_accessible_ignores_expiry(self, app_db, client, hello_project):
        app_db.create_invite("OLDONE", hello_project, None, datetime.utcnow() - timedelta(days=1), max_shares=1)
        Session().role = "user"
        app_db.record_invite_redemption(Session().user, hello_project, app_db.get_invite_by_code("OLDONE").id, datetime.utcnow())

        response = client.post("/api/projects/by-invite/OLDONE")

        assert response.status_code == 200
        assert response.json() == {"project_id": hello_project}
