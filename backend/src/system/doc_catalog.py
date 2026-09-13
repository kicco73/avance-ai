"""What `/api/core/docs/{name}` can answer.

A slug names a subject, never a package. The document behind it is
whatever this build has to say about that subject: the core's own file,
plus a section from every installed skill that carries one — assembled
the way the skills page already is, so a skill that was not copied
contributes nothing and a subject nothing is left to say about does not
exist as a slug at all.

That is what keeps the core from naming a skill here, and what keeps a
delivery from promising a document it cannot render.
"""
from __future__ import annotations

from pathlib import Path

from system import skills

DOCS_DIR = Path(__file__).resolve().parent.parent / "docs"

#: The core's own reference material, by slug. Not every file in docs/ —
#: the conventions written for whoever works in this repo are not
#: reference material the product serves.
CORE_DOCS = {
    "project-specs": "PROJECT_SPECS.md",
    "metrics": "METRICS.md",
    "skills": "SKILLS.md",
}


class Doc:

    def __init__(self, file_name: str) -> None:
        self.file_name = file_name

    def render(self) -> str:
        parts = [self._core(), skills.documentation(file_name=self.file_name)]
        return "\n\n".join(part for part in parts if part)

    def _core(self) -> str:
        try:
            return (DOCS_DIR / self.file_name).read_text(encoding="utf-8").strip()
        except FileNotFoundError:
            return ""


class SkillsDoc(Doc):

    def render(self) -> str:
        return "\n".join([self._core(), "", skills.documentation(), ""])


def catalog() -> dict[str, Doc]:
    """Every slug this build can answer for. Derived at each call, because
    what a build installed is what decides it."""
    files = {**skills.documented_slugs(), **CORE_DOCS}
    return {
        slug: SkillsDoc(file_name) if slug == "skills" else Doc(file_name)
        for slug, file_name in files.items()
    }
