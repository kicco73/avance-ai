"""On-disk cache for generated message audio, scoped per model, with a
retention policy applied both on write (keep at most MAX_FILES_PER_MODEL)
and on read (drop everything older than whatever was just served) — see
AudioStore. Also tracks in-flight generations (LiveAudioGeneration) so a
GET arriving while one is still running can be fed chunks in real time
instead of waiting for the completed file — see ChatService. No
singleton: one instance is constructed in main.py and passed explicitly
to whatever needs it (ChatService), same style as
ModelWatcher/ChatWsAdapter.
"""
from __future__ import annotations

import asyncio
import logging
import tempfile
from pathlib import Path
from typing import AsyncIterator

logger = logging.getLogger(__name__)

MAX_FILES_PER_MODEL = 10


class LiveAudioGeneration(object):
    """One in-flight audio generation for a single message id: an
    append-only log of the chunks produced so far, plus an Event any
    reader can wait on to notice new ones. stream_from(0) — the only way
    this prototype uses it — replays everything already appended before
    catching up to the live tail, so a client joining after generation
    has already produced some chunks still gets all of them, not just
    whatever comes after it connects.

    push()/finish() are called from ChatService's background generation
    task, always via the event loop (never from the worker thread actually
    talking to the provider — see asyncio.to_thread there), so no
    additional locking is needed here: everything below only ever runs on
    the event loop thread.

    Single-writer. Any number of readers technically works (each tracks
    its own index into the same append-only list), but only one is ever
    expected in practice — see the module/task's own "single client"
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
        # In-flight generations, keyed by message id — purely in-memory,
        # separate from the on-disk cache below, and gone the moment
        # generation finishes (see finish_live_generation).
        self._live: dict[int, LiveAudioGeneration] = {}

    def start_live_generation(self, message_id: int) -> LiveAudioGeneration:
        generation = LiveAudioGeneration()
        self._live[message_id] = generation
        return generation

    def finish_live_generation(self, message_id: int) -> None:
        self._live.pop(message_id, None)

    def get_live_generation(self, message_id: int) -> LiveAudioGeneration | None:
        return self._live.get(message_id)

    def _model_dir(self, model_name: str) -> Path:
        return self._base_dir / model_name

    def save(self, model_name: str, message_id: int, data: bytes) -> None:
        model_dir = self._model_dir(model_name)
        model_dir.mkdir(parents=True, exist_ok=True)
        (model_dir / f"{message_id}.wav").write_bytes(data)
        self._enforce_max_files(model_dir)

    def read_and_purge_older(self, model_name: str, message_id: int) -> bytes | None:
        """Returns the audio for `message_id` if it's still on disk, or
        None otherwise (never generated, or already purged by either
        policy below). On a hit, also deletes every other file for this
        same model older than the one just served — only it and anything
        more recent survive."""
        path = self._model_dir(model_name) / f"{message_id}.wav"
        if not path.is_file():
            return None

        data = path.read_bytes()
        served_mtime = path.stat().st_mtime
        for other in self._model_dir(model_name).iterdir():
            if other != path and other.stat().st_mtime < served_mtime:
                other.unlink(missing_ok=True)
        return data

    @staticmethod
    def _enforce_max_files(model_dir: Path, max_files: int = MAX_FILES_PER_MODEL) -> None:
        files = sorted(model_dir.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True)
        for stale in files[max_files:]:
            stale.unlink(missing_ok=True)
