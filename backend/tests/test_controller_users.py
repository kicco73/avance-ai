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


@pytest.mark.regression
def test_put_user_role_updates_the_role(client, app_db):
    app_db.get_or_create_user("google", "sub-1", "alice@example.com", "Alice", None)

    response = client.put("/api/users/alice@example.com/role", json={"role": "supervisor"})

    assert response.status_code == 200
    assert response.json()["role"] == "supervisor"
    assert app_db.get_user_by_email("alice@example.com")["role"] == "supervisor"


@pytest.mark.regression
def test_put_user_role_rejects_an_unknown_role(client, app_db):
    app_db.get_or_create_user("google", "sub-1", "alice@example.com", "Alice", None)

    response = client.put("/api/users/alice@example.com/role", json={"role": "superadmin"})

    assert response.status_code == 422
