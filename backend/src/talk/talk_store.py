"""On-disk cache for generated audio, content-addressed by a
caller-supplied key, retained at most MAX_FILES and pruned of anything
older than what was just served. Also tracks in-flight generations so a
second request for the same key mid-generation joins it instead of
triggering a duplicate."""
from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path
from typing import AsyncIterator

from logging_factory import LoggerFactory

logger = LoggerFactory.get_logger(__name__)

MAX_FILES = 10


class LiveTalkGeneration(object):
    """One in-flight generation for a single cache key: an append-only
    log of chunks plus an Event readers wait on. push()/finish() are
    only ever called from the event loop, so no locking is needed."""

    def __init__(self) -> None:
        self._chunks: list[bytes] = []
        self._done = False
        self._new_data = asyncio.Event()

    def push(self, chunk: bytes) -> None:
        self._chunks.append(chunk)
        self._new_data.set()

    def finish(self) -> None:
        self._done = True
        self._new_data.set()

    async def stream_from(self, start_index: int = 0) -> AsyncIterator[bytes]:
        i = start_index
        while True:
            while i < len(self._chunks):
                yield self._chunks[i]
                i += 1
            if self._done:
                return
            self._new_data.clear()
            # A chunk (or finish()) may have landed between the while
            # above and clear() — recheck instead of awaiting forever.
            if i < len(self._chunks) or self._done:
                continue
            await self._new_data.wait()


class TalkStore(object):
    def __init__(self) -> None:
        # Kept alive for the process's whole lifetime (not a `with` block,
        # which would delete it immediately) — cleans itself up on
        # interpreter exit.
        self._tempdir = tempfile.TemporaryDirectory(prefix="avance-talk-")
        self._base_dir = Path(self._tempdir.name)
        # In-flight generations, keyed by cache key — purely in-memory,
        # separate from the on-disk cache below, and gone the moment
        # generation finishes (see finish_live_generation).
        self._live: dict[str, LiveTalkGeneration] = {}

    def start_live_generation(self, key: str) -> LiveTalkGeneration:
        generation = LiveTalkGeneration()
        self._live[key] = generation
        return generation

    def finish_live_generation(self, key: str) -> None:
        self._live.pop(key, None)

    def get_live_generation(self, key: str) -> LiveTalkGeneration | None:
        return self._live.get(key)

    def save(self, key: str, data: bytes) -> None:
        self._base_dir.mkdir(parents=True, exist_ok=True)
        (self._base_dir / f"{key}.wav").write_bytes(data)
        self._enforce_max_files()

    def read_and_purge_older(self, key: str) -> bytes | None:
        """Returns the cached audio for `key`, or None. On a hit, also
        deletes every other cached file older than the one just served."""
        path = self._base_dir / f"{key}.wav"
        if not path.is_file():
            return None

        data = path.read_bytes()
        served_mtime = path.stat().st_mtime
        for other in self._base_dir.iterdir():
            if other != path and other.stat().st_mtime < served_mtime:
                other.unlink(missing_ok=True)
        return data

    def _enforce_max_files(self, max_files: int = MAX_FILES) -> None:
        files = sorted(self._base_dir.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True)
        for stale in files[max_files:]:
            stale.unlink(missing_ok=True)
