from __future__ import annotations

import logging
import sys
from types import FrameType


def _find_caller_frame() -> FrameType | None:
    skip_files = (logging.__file__, __file__)
    frame = sys._getframe(1)
    while frame is not None and frame.f_code.co_filename in skip_files:
        frame = frame.f_back
    return frame


class _OriginColorFormatter(logging.Formatter):

    _LEVEL_COLORS = {
        logging.DEBUG: "\033[36m",
        logging.INFO: "\033[32m",
        logging.WARNING: "\033[33m",
        logging.ERROR: "\033[31m",
        logging.CRITICAL: "\033[1;31m",
    }
    _RESET = "\033[0m"

    def __init__(self, fmt: str, use_color: bool) -> None:
        super().__init__(fmt)
        self._use_color = use_color

    def format(self, record: logging.LogRecord) -> str:
        record.origin = self._resolve_origin(record)
        if self._use_color:
            color = self._LEVEL_COLORS.get(record.levelno, "")
            record.levelname = f"{color}{record.levelname}{self._RESET}"
        return super().format(record)

    @staticmethod
    def _resolve_origin(record: logging.LogRecord) -> str:
        frame = _find_caller_frame()
        if frame is not None:
            qualname = getattr(frame.f_code, "co_qualname", record.funcName)
            if qualname != record.funcName:
                return qualname
        return f"{record.module}.{record.funcName}"


class LoggerFactory:

    _configured = False

    @classmethod
    def get_logger(cls, name: str) -> logging.Logger:
        cls._configure_once()
        return logging.getLogger(name)

    @classmethod
    def _configure_once(cls) -> None:
        if cls._configured:
            return
        cls._configured = True
        handler = logging.StreamHandler()
        use_color = getattr(handler.stream, "isatty", lambda: False)()
        handler.setFormatter(_OriginColorFormatter(
            "%(levelname)s: %(origin)s(): %(message)s", use_color=use_color,
        ))
        root = logging.getLogger()
        root.setLevel(logging.INFO)
        root.handlers = [handler]
