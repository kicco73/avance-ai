from __future__ import annotations

from system.logging_factory import LoggerFactory

logger = LoggerFactory.get_logger(__name__)


class BuildCursor:
    def __init__(self) -> None:
        self.line: int | None = None
        self.section: str | None = None
        self.warnings: list[dict] = []

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

    @staticmethod
    def line_of(parent, key: str) -> int | None:
        try:
            return parent.lc.key(key)[0]
        except (AttributeError, KeyError, TypeError):
            return None

    @staticmethod
    def own_line(node) -> int | None:
        return getattr(getattr(node, "lc", None), "line", None)
