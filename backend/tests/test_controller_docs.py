"""GET /api/docs/{name} — raw markdown content of the fixed reference docs
listed in controllers/chat_controller.py's DOC_FILES.
"""
from __future__ import annotations

import pytest

from controllers.chat_controller import DOC_FILES

pytestmark = pytest.mark.contract


@pytest.mark.parametrize("name", list(DOC_FILES))
def test_get_doc_returns_the_files_own_content(client, name):
    response = client.get(f"/api/docs/{name}")

    assert response.status_code == 200
    assert response.json()["content"]


def test_get_doc_is_404_for_an_unknown_name(client):
    response = client.get("/api/docs/not-a-real-doc")
    assert response.status_code == 404
