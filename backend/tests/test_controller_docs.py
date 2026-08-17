"""GET /api/docs/{name} — raw markdown content of one of src/docs' fixed
reference docs (see controller.py's own DOC_FILES), backing each "(?)"
documentation button (EditProjectView.vue's own, next to Save; the
Inspector's Metrics/Performance tabs).
"""
from __future__ import annotations

import pytest

from controller import DOC_FILES

pytestmark = pytest.mark.contract


@pytest.mark.parametrize("name", list(DOC_FILES))
def test_get_doc_returns_the_files_own_content(client, name):
    response = client.get(f"/api/docs/{name}")

    assert response.status_code == 200
    assert response.json()["content"]


def test_get_doc_is_404_for_an_unknown_name(client):
    response = client.get("/api/docs/not-a-real-doc")
    assert response.status_code == 404
