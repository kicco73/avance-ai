"""GET /api/core/docs/{name} — the reference documents this build can
answer for, each rendered by the document itself: the core's own file,
plus a section from every installed skill that carries one.

Nothing here is written down. The slugs come from the catalog, which
derives them from what is installed, so a build that left a skill out is
never asserted to serve a document that left with it.

It lives inside `src/docs/` because that is the directory it asserts the
content of, and no build copies a directory of that name: a delivery
keeps the routes, has nothing behind them, and does not carry a test that
would say so. That is the open question, not a passing state —
where the product's own reference material lives has still to be decided.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from system import doc_catalog, skills

SRC_ROOT = Path(skills.__file__).resolve().parent.parent
DOCUMENTED = [skill for skill in skills.discover() if skill.documentation()]

pytestmark = pytest.mark.contract


@pytest.mark.parametrize("name", list(doc_catalog.catalog()))
def test_get_doc_returns_the_files_own_content(client, name):
    response = client.get(f"/api/core/docs/{name}")

    assert response.status_code == 200
    assert response.json()["content"]


def test_get_doc_is_404_for_an_unknown_name(client):
    response = client.get("/api/core/docs/not-a-real-doc")
    assert response.status_code == 404


def test_the_skills_doc_carries_a_section_from_every_installed_skill(client):
    content = client.get("/api/core/docs/skills").json()["content"]

    assert "# The skills" in content
    for skill in skills.discover():
        section = skill.documentation()
        if section:
            assert section in content


def _source_root_with(tmp_path, packages) -> Path:
    source_root = tmp_path / "src"
    source_root.mkdir()
    for package in packages:
        (source_root / package).symlink_to(SRC_ROOT / package)
    return source_root


@pytest.mark.parametrize("skill", DOCUMENTED, ids=lambda skill: skill.package)
def test_the_skills_page_carries_only_the_skills_a_build_copied(tmp_path, skill):
    root = _source_root_with(tmp_path, [skill.package])

    assert skills.documentation(root) == skill.documentation()


def test_the_skills_page_joins_the_sections_of_everything_that_was_copied(tmp_path):
    root = _source_root_with(tmp_path, [skill.package for skill in DOCUMENTED])

    assert skills.documentation(root) == "\n\n".join(skill.documentation() for skill in DOCUMENTED)


def test_a_document_carries_the_section_of_every_installed_skill_that_serves_that_slug():
    catalog = doc_catalog.catalog()

    found = False
    for skill in skills.discover():
        for slug, file_name in skill.docs().items():
            assert slug in catalog, f"{skill.package} serves {slug!r}, the catalog does not"
            assert skill.documentation(file_name) in catalog[slug].render()
            found = True
    assert found, "no installed skill serves a document — the assertion above proved nothing"
