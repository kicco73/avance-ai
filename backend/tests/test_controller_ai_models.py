from __future__ import annotations

import pytest

pytestmark = pytest.mark.contract


def test_get_ai_models_response_shape(client):
    body = client.get("/api/ai/models").json()
    assert set(body) == {"auto", "current_index", "models"}


def test_post_ai_model_selection_returns_the_updated_info(client):
    body = client.post("/api/ai/models/selection", json={"index": 0}).json()
    assert set(body) == {"auto", "current_index", "models"}


def test_get_ai_test_models_response_shape(client):
    body = client.get("/api/ai/models/test").json()
    assert set(body) == {"auto", "current_index", "models"}


def test_post_ai_test_model_selection_returns_the_updated_info(client):
    body = client.post("/api/ai/models/test/selection", json={"index": 0}).json()
    assert set(body) == {"auto", "current_index", "models"}
