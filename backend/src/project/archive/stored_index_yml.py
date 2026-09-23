from __future__ import annotations

from automaton.automaton_builder import AutomatonBuilder
from automaton.build_error import AutomatonBuildError
from automaton.file_types import ProjectFileTypes
from automaton.index_yml_modernizer import IndexYmlModernizer
from db import Db
from system.logging_factory import LoggerFactory

logger = LoggerFactory.get_logger(__name__)


class StoredIndexYml:
    """One stored revision's index.yml, brought up to date where a build
    refuses a spelling the format has only moved past.

    The design view repairs what a person opens, and tells them what it
    changed. That is not enough on its own: what a product serves is the
    *published* revision, which nobody opens, so tightening the format
    would take every project written before it out of service until its
    author happened to visit and publish. A revision that fails to build
    is asked once whether the modernizer can settle it, and is rewritten
    in place only if the answer builds — same revision, same automaton,
    one less reason for a product to be down."""

    def __init__(self, db: Db, project_id: str, revision: int) -> None:
        self._db = db
        self._project_id = project_id
        self._revision = revision

    def rebuilt(self, contents: dict, refusal: AutomatonBuildError):
        repaired = IndexYmlModernizer().modernize(contents["index.yml"])
        if not repaired.fixes:
            raise refusal
        try:
            automaton = AutomatonBuilder().build(
                {**contents, "index.yml": repaired.text}, legacy_project_id=self._project_id,
            )
        except AutomatonBuildError as remaining:
            raise AutomatonBuildError(
                f"{remaining} (still refused after rewriting {'; '.join(repaired.fixes)})",
                section=remaining.section,
            ) from refusal
        self._store(repaired.text, repaired.fixes)
        contents["index.yml"] = repaired.text
        return automaton

    def _store(self, text: str, fixes: tuple[str, ...]) -> None:
        self._db.write_archive_at_revision(
            self._project_id, "index.yml", self._revision,
            text.encode("utf-8"), ProjectFileTypes.of("index.yml").content_type,
        )
        logger.warning(
            "Project '%s', revision %s: index.yml rewritten in place — %s",
            self._project_id, self._revision, ", ".join(fixes),
        )
