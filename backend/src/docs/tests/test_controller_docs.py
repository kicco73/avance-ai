"""GET /api/core/docs/{name} — the fixed reference docs listed
in system/doc_catalog.py's DOCS, each rendered by the document
itself: a file on disk, or the skills page every installed skill writes
one section of.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from system.doc_catalog import DOCS

pytestmark = pytest.mark.contract


@pytest.mark.parametrize("name", list(DOCS))
def test_get_doc_returns_the_files_own_content(client, name):
    response = client.get(f"/api/core/docs/{name}")

    assert response.status_code == 200
    assert response.json()["content"]


def test_get_doc_is_404_for_an_unknown_name(client):
    response = client.get("/api/core/docs/not-a-real-doc")
    assert response.status_code == 404


def test_the_skills_doc_carries_a_section_from_every_installed_skill(client):
    from system import skills

    content = client.get("/api/core/docs/skills").json()["content"]

    assert "# The skills" in content
    for skill in skills.discover():
        section = skill.documentation()
        if section:
            assert section in content


def test_the_skills_page_carries_only_the_skills_a_build_copied(tmp_path):
    from system import skills

    source_root = tmp_path / "src"
    source_root.mkdir()
    real = Path(skills.__file__).resolve().parent.parent
    for package in ("talk", "listen"):
        (source_root / package).symlink_to(real / package)

    page = skills.documentation(source_root)

    assert "## Talk" in page
    assert "## Listen" in page
    assert "WhatsApp" not in page
    assert "## Platform" not in page
