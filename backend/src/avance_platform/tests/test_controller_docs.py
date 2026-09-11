"""GET /api/skills/platform/docs/{name} — the fixed reference docs listed
in avance_platform/doc_catalog.py's DOCS, each rendered by the document
itself: a file on disk, or the skills page every installed skill writes
one section of.
"""
from __future__ import annotations

import pytest

from avance_platform.doc_catalog import DOCS

pytestmark = pytest.mark.contract


@pytest.mark.parametrize("name", list(DOCS))
def test_get_doc_returns_the_files_own_content(client, name):
    response = client.get(f"/api/skills/platform/docs/{name}")

    assert response.status_code == 200
    assert response.json()["content"]


def test_get_doc_is_404_for_an_unknown_name(client):
    response = client.get("/api/skills/platform/docs/not-a-real-doc")
    assert response.status_code == 404


def test_the_skills_doc_carries_a_section_from_every_installed_skill(client):
    from system import skills

    content = client.get("/api/skills/platform/docs/skills").json()["content"]

    assert "# The skills" in content
    for skill in skills.discover():
        section = skill.documentation()
        if section:
            assert section in content


def test_the_skills_doc_names_no_skill_that_is_not_installed(client, tmp_path):
    from system import skills

    installed = {entry["package"] for entry in skills.installed()}

    assert "whatsapp" in installed
    assert skills.documentation(tmp_path) == ""
