"""Filesystem watcher for backend/models/: detects a model edited
directly on disk (not through the upload API) and hot-reloads it via
ModelService.refresh_model_from_disk — the single shared "activate and
reset" function also used by the upload path. Disabled unless explicitly
enabled (see main.py's MODEL_FILE_WATCH_ENABLED). No singleton: the one
instance is constructed in main.py with its dependencies passed in
explicitly, same style as chat_ws_adapter.
"""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Awaitable, Callable

from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

from model.model_service import MODELS_DIR, CommitCallback, ModelService

logger = logging.getLogger(__name__)

# Called with the model_name after refresh_model_from_disk() reset it
# because it was the active model — used to push the websocket notice.
OnActiveModelReset = Callable[[str], Awaitable[None]]


class _ModelDirEventHandler(FileSystemEventHandler):
    def __init__(self, on_event: Callable[[str], None]) -> None:
        self._on_event = on_event

    def on_created(self, event) -> None:
        if not event.is_directory:
            self._on_event(event.src_path)

    def on_modified(self, event) -> None:
        if not event.is_directory:
            self._on_event(event.src_path)

    def on_moved(self, event) -> None:
        if not event.is_directory:
            self._on_event(event.dest_path)


class ModelWatcher(object):
    """Watches MODELS_DIR recursively on its own thread (watchdog's
    default) and, for every real content change, hands the model name off
    to ModelService.refresh_model_from_disk() on the asyncio event loop."""

    def __init__(
        self,
        model_service: ModelService,
        commit: CommitCallback,
        on_active_model_reset: OnActiveModelReset | None = None,
    ) -> None:
        self._model_service = model_service
        self._commit = commit
        self._on_active_model_reset = on_active_model_reset
        self._observer = Observer()
        self._loop: asyncio.AbstractEventLoop | None = None

    def start(self) -> None:
        """Call once, from inside the running asyncio loop (a FastAPI
        startup hook) — captures that loop so events delivered on
        watchdog's own thread can safely schedule work back onto it via
        run_coroutine_threadsafe, instead of touching asyncio state
        directly from a foreign thread."""
        self._loop = asyncio.get_running_loop()
        MODELS_DIR.mkdir(parents=True, exist_ok=True)
        handler = _ModelDirEventHandler(self._on_fs_event)
        self._observer.schedule(handler, str(MODELS_DIR), recursive=True)
        self._observer.start()
        logger.info("Model file watcher started on %s", MODELS_DIR)

    def stop(self) -> None:
        self._observer.stop()
        self._observer.join()

    def _on_fs_event(self, path: str) -> None:
        # Runs on watchdog's own thread — do only cheap, thread-safe work
        # here (path parsing), then hand off the actual reload.
        try:
            relative = Path(path).relative_to(MODELS_DIR)
        except ValueError:
            return
        if not relative.parts:
            return

        model_name = relative.parts[0]
        if model_name.startswith("."):
            # The upload path's own staging dirs/temp files (e.g.
            # .tmp_<uuid>.yml or .tmp_<uuid>/) — never a real model.
            return

        if self._loop is None:
            return
        asyncio.run_coroutine_threadsafe(self._refresh(model_name), self._loop)

    async def _refresh(self, model_name: str) -> None:
        was_active_and_reset = await self._model_service.refresh_model_from_disk(model_name, self._commit)
        if was_active_and_reset and self._on_active_model_reset:
            await self._on_active_model_reset(model_name)
