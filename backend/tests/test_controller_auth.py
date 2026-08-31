"""GET /api/auth/pending-status (AuthController.get_pending_status /
AuthService.is_invite_exempt) — App.vue's own TermsView-vs-
InviteRequiredView gate for a pending identity."""
from __future__ import annotations

import pytest

from session import Session

pytestmark = pytest.mark.contract


@pytest.mark.regression
def test_a_pre_wired_admin_is_invite_exempt(client):
    """Regression: an admin who erased their own data must still be
    able to re-register with no invite link on re-login (see
    test_auth_service.py's own is_invite_exempt regression test for the
    service-level half of this)."""
    Session().user = "enrico.carniani@gmail.com"

    response = client.get("/api/auth/pending-status")

    assert response.status_code == 200
    assert response.json() == {"invite_exempt": True}


@pytest.mark.regression
def test_a_regular_identity_is_not_invite_exempt(client):
    Session().user = "stranger@example.com"

    response = client.get("/api/auth/pending-status")

    assert response.status_code == 200
    assert response.json() == {"invite_exempt": False}
