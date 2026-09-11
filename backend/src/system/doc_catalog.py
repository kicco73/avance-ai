from __future__ import annotations

from pathlib import Path

from system import skills

DOCS_DIR = Path(__file__).resolve().parent.parent / "docs"


class FileDoc:

    def __init__(self, file_name: str) -> None:
        self.file_name = file_name

    def render(self) -> str:
        return (DOCS_DIR / self.file_name).read_text(encoding="utf-8")


class SkillsDoc(FileDoc):

    def render(self) -> str:
        return "\n".join([super().render().rstrip(), "", skills.documentation(), ""])


DOCS = {
    "project-specs": FileDoc("PROJECT_SPECS.md"),
    "metrics": FileDoc("METRICS.md"),
    "benchmark": FileDoc("BENCHMARK.md"),
    "markdown-guide": FileDoc("MARKDOWN_GUIDE.md"),
    "session-specs": FileDoc("SESSION_SPECS.md"),
    "skin-specs": FileDoc("SKIN_SPECS.md"),
    "skills": SkillsDoc("SKILLS.md"),
}
