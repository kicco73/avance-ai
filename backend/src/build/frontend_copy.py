"""The frontend half of a build: the same skills, the same absence.

A backend drops a skill by not copying `backend/src/<package>/`. The
frontend drops the same skill by not copying `frontend/src/skills/<key>/`,
and its registry finds what is there instead of reading a list — so the
bundle that comes out has no code, no route and no name belonging to
anything that was left out.

Two things are deliberately different from the backend's own copy. The
directories are named by *key* while `excluded_skills` names *packages*
(`avance_platform` declares `key = "platform"`), so the translation goes
through the roster rather than assuming they match. And the pruning
happens after the copy rather than through an ignore function, because
that roster is what the translation needs.

What is delivered is the source, pruned and ready to build the same way
the Dockerfile already builds it (`npm ci && npm run build`) — not a
compiled `dist/`. Compiling here would put an `npm ci` inside every
build, minutes of it, and would make a build require node in an
environment that otherwise needs none. What this step does instead is
prove the pruning was clean: nothing left behind may name a skill that
was dropped, which is the same rule the frontend's own contract test
holds on the repo.
"""
from __future__ import annotations

import shutil
from pathlib import Path

from system.logging_factory import LoggerFactory

logger = LoggerFactory.get_logger(__name__)

_SOURCE_SUFFIXES = {".js", ".vue", ".css", ".html", ".json", ".md"}

_FRONTEND_COPY_IGNORE = shutil.ignore_patterns("node_modules", "dist", ".vite", "*.log")


class FrontendBuildError(Exception):
    pass


class FrontendCopy:

    def __init__(self, source: Path, destination: Path, excluded_skills: list[str]) -> None:
        self._source = source
        self._destination = destination
        self._excluded_skills = excluded_skills

    @property
    def skills_dir(self) -> Path:
        return self._destination / "src" / "skills"

    def build(self) -> dict:
        self._copy()
        excluded_keys = self._excluded_keys()
        dropped = self._prune_skills(excluded_keys)
        leaked = self._leaked(excluded_keys)
        if leaked:
            raise FrontendBuildError(
                f"The copied frontend still names {', '.join(sorted(leaked))} — it was supposed to be left out."
            )
        return {"path": str(self._destination), "dropped_skills": sorted(dropped)}

    def _copy(self) -> None:
        shutil.rmtree(self._destination, ignore_errors=True)
        shutil.copytree(self._source, self._destination, ignore=_FRONTEND_COPY_IGNORE)

    def _excluded_keys(self) -> set[str]:
        """The keys of every excluded package, whether or not it owns a
        directory here. A skill with no frontend of its own is still gone
        from the backend, so the frontend must stop naming it just the
        same — reading the roster instead of the directory listing is
        what makes that true for `platform`, whose routes the core still
        calls while `frontend/src/skills/platform/` does not exist."""
        from system import skills

        excluded = set(self._excluded_skills)
        return {entry["key"] for entry in skills.installed() if entry["package"] in excluded}

    def _prune_skills(self, excluded_keys: set[str]) -> set[str]:
        dropped = set()
        for key in excluded_keys:
            directory = self.skills_dir / key
            if not directory.is_dir():
                continue
            shutil.rmtree(directory)
            dropped.add(key)
        logger.info("frontend pruned of %s", ", ".join(sorted(dropped)) or "nothing")
        return dropped

    def _leaked(self, excluded_keys: set[str]) -> set[str]:
        """Whatever an excluded skill left behind in what is delivered. An
        import path (`skills/<key>/`) or a route (`/api/skills/<key>/`) is
        what would actually break, or leak — the same substring covers
        both. A prose mention of a name is neither."""
        sources = [path for path in self._destination.rglob("*") if path.suffix in _SOURCE_SUFFIXES]
        contents = "\n".join(path.read_text(encoding="utf-8", errors="ignore") for path in sources)
        return {key for key in excluded_keys if f"skills/{key}/" in contents}
