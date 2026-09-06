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


def _create_invite(client, project_id) -> str:
    response = client.post(f"/api/projects/{project_id}/invites")
    assert response.status_code == 200, response.text
    return response.json()


def test_every_call_creates_a_fresh_invite_with_its_own_code_and_the_configured_defaults(client, hello_project):
    body = _create_invite(client, hello_project)

    assert len(body["code"]) == 6
    assert body["code"].isalnum()
    assert body["max_shares"] == 3  # ProjectService's own default
    assert body["expires_at"]

    assert _create_invite(client, hello_project)["code"] != body["code"]
    assert client.post("/api/projects/does-not-exist/invites").status_code == 404


def test_resolving_a_code_reports_its_project_or_none_rather_than_404ing(client, hello_project):
    code = _create_invite(client, hello_project)["code"]

    response = client.post(f"/api/projects/by-invite/{code}")
    assert response.status_code == 200
    assert response.json() == {"project_id": hello_project}

    unknown = client.post("/api/projects/by-invite/NOSUCH")
    assert unknown.status_code == 200
    assert unknown.json() == {"project_id": None}


class TestPostResolveInviteCodeAsUser:
    """The default test session role (supervisor) isn't gated by
    UserProject, so these override Session().role directly — same
    pattern as test_controller_chat_truncate.py's own role downgrade."""

    def test_a_valid_link_grants_access_while_an_expired_one_is_forbidden_without_existing_access(self, app_db, client, hello_project):
        code = _create_invite(client, hello_project)["code"]
        app_db.create_invite("EXPIR1", hello_project, None, datetime.utcnow() - timedelta(days=1), max_shares=3)
        Session().role = "user"

        expired = client.post("/api/projects/by-invite/EXPIR1")
        assert expired.status_code == 403
        assert app_db.user_has_project_access(Session().user, hello_project) is False

        response = client.post(f"/api/projects/by-invite/{code}")
        assert response.status_code == 200
        assert response.json() == {"project_id": hello_project}
        assert app_db.user_has_project_access(Session().user, hello_project) is True

        # Revisiting a project already accessible ignores expiry entirely.
        assert client.post("/api/projects/by-invite/EXPIR1").status_code == 200
