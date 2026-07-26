"""On-disk cache for generated audio, content-addressed by a caller-
supplied key (see AudioService.generate — a hash of the text), with a
retention policy applied both on write (keep at most MAX_FILES) and on
read (drop everything older than whatever was just served). Also tracks
in-flight generations (LiveAudioGeneration) so a second request for the
same key arriving mid-generation gets fed chunks in real time instead of
triggering a duplicate one. Owned and constructed by AudioService, not
a singleton.
"""
from __future__ import annotations

import asyncio
import logging
import tempfile
from pathlib import Path
from typing import AsyncIterator

logger = logging.getLogger(__name__)

MAX_FILES = 10


class LiveAudioGeneration(object):
    """One in-flight audio generation for a single cache key: an
    append-only log of the chunks produced so far, plus an Event any
    reader can wait on to notice new ones. stream_from(0) — the only way
    this prototype uses it — replays everything already appended before
    catching up to the live tail, so a client joining after generation
    has already produced some chunks still gets all of them, not just
    whatever comes after it connects.

    push()/finish() are only ever called from the event loop (never from
    the worker thread actually talking to the provider — see
    asyncio.to_thread in cascade.py), so no additional locking is needed.

    Single-writer. Any number of readers technically works (each tracks
    its own index into the same append-only list), but only one is ever
    expected in practice — see the module's own "single client"
    simplification."""

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


class AudioStore(object):
    def __init__(self) -> None:
        # Kept alive for the process's whole lifetime (not a `with` block,
        # which would delete it immediately) — the standard library's own
        # mechanism for a directory that cleans itself up on interpreter
        # exit, rather than a hand-managed folder under the project.
        self._tempdir = tempfile.TemporaryDirectory(prefix="avance-audio-")
        self._base_dir = Path(self._tempdir.name)
        # In-flight generations, keyed by cache key — purely in-memory,
        # separate from the on-disk cache below, and gone the moment
        # generation finishes (see finish_live_generation).
        self._live: dict[str, LiveAudioGeneration] = {}

    def start_live_generation(self, key: str) -> LiveAudioGeneration:
        generation = LiveAudioGeneration()
        self._live[key] = generation
        return generation

    def finish_live_generation(self, key: str) -> None:
        self._live.pop(key, None)

    def get_live_generation(self, key: str) -> LiveAudioGeneration | None:
        return self._live.get(key)

    def save(self, key: str, data: bytes) -> None:
        self._base_dir.mkdir(parents=True, exist_ok=True)
        (self._base_dir / f"{key}.wav").write_bytes(data)
        self._enforce_max_files()

    def read_and_purge_older(self, key: str) -> bytes | None:
        """Returns the cached audio for `key`, or None if there isn't any
        (never generated, or already purged by either policy below). On
        a hit, also deletes every other cached file older than the one
        just served — only it and anything more recent survive."""
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
