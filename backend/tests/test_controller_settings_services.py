from __future__ import annotations

import pytest


@pytest.mark.contract
def test_get_services_returns_the_configured_snapshot_verbatim(client):
    response = client.get("/api/core/settings/services")

    assert response.status_code == 200
    body = response.json()
    assert set(body.keys()) == {"chat", "testing", "ai", "talk", "listen", "database", "build"}
    assert body["database"]["url"] == "sqlite:///test.db"
    assert body["ai"]["providers"][0]["driver"] == "fake"
