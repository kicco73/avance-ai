"""GET /api/users (UserController.get_users / Db.list_users)."""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.contract


@pytest.mark.regression
def test_list_users_includes_a_created_user(client, app_db):
    app_db.get_or_create_user("google", "sub-1", "alice@example.com", "Alice", None)

    response = client.get("/api/users")

    assert response.status_code == 200
    emails = [row["email"] for row in response.json()["users"]]
    assert emails == ["alice@example.com"]
