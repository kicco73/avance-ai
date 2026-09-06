from __future__ import annotations

from logging_factory import LoggerFactory

logger = LoggerFactory.get_logger(__name__)


class BuildCursor:
    def __init__(self) -> None:
        self.line: int | None = None
        self.section: str | None = None
        self.warnings: list[str] = []

    def at(self, line: int | None, section: str) -> None:
        self.line = line
        self.section = section

    def warn(self, message: str) -> None:
        logger.warning("Build warning: %s", message)
        self.warnings.append(message)

    @staticmethod
    def line_of(parent, key: str) -> int | None:
        try:
            return parent.lc.key(key)[0]
        except (AttributeError, KeyError, TypeError):
            return None

    @staticmethod
    def own_line(node) -> int | None:
        return getattr(getattr(node, "lc", None), "line", None)
