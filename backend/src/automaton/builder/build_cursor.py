from __future__ import annotations

from automaton.build_error import AutomatonBuildError
from system.logging_factory import LoggerFactory

logger = LoggerFactory.get_logger(__name__)


class BuildCursor:
    def __init__(self) -> None:
        self.line: int | None = None
        self.section: str | None = None
        self.warnings: list[dict] = []
        self.rejections: list[dict] = []

    def at(self, line: int | None, section: str) -> None:
        self.line = line
        self.section = section

    def warn(self, message: str, line: int | None = None) -> None:
        """Where it was found travels with it: a warning nobody can find
        is a warning nobody acts on, and the editor jumps to the line the
        same way it jumps to a build error (see AutomatonBuildError,
        which has carried line/section all along)."""
        logger.warning("Build warning: %s", message)
        self.warnings.append({
            "message": message,
            "line": self.line if line is None else line,
            "section": self.section,
        })

    def reject(self, message: str, line: int | None = None) -> None:
        """A problem the rest of the pass can be carried out in spite of
        — a field nobody reads, say. Recorded and reported together at
        the end (see raise_if_rejected) rather than raised where it is
        found: fixing an index.yml one rejection per build, with a whole
        pass between each, is how a five-field mistake takes five rounds."""
        self.rejections.append({
            "message": message,
            "line": self.line if line is None else line,
            "section": self.section,
        })

    def raise_if_rejected(self) -> None:
        """One refusal carrying every problem the pass found, each with
        its own line: `problems` is the same shape a warning has, so the
        editor draws them the same way — one clickable line per problem,
        landing on the key that caused it.

        The message itself only counts them. Every string that is not
        that list — a log line, an admin's "project broken" notice, a row
        in Manage projects — would otherwise carry all of them run
        together into a paragraph nobody can read and nobody can click."""
        if not self.rejections:
            return
        self.rejections.sort(key=lambda rejection: (rejection["line"] is None, rejection["line"]))
        first = self.rejections[0]
        message = (
            first["message"] if len(self.rejections) == 1
            else f"{len(self.rejections)} problems in index.yml"
        )
        raise AutomatonBuildError(
            message, line=first["line"], section=first["section"], problems=list(self.rejections),
        )

    @staticmethod
    def line_of(parent, key: str) -> int | None:
        try:
            return parent.lc.key(key)[0]
        except (AttributeError, KeyError, TypeError):
            return None

    @staticmethod
    def own_line(node) -> int | None:
        return getattr(getattr(node, "lc", None), "line", None)
