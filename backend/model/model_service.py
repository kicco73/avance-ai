"""Validating/staging/committing model activations, uploads, and
deletions — plus every db.py access tied to "which model/state is
active", encapsulated here so other layers never reach into db.py
themselves for that.
"""
from __future__ import annotations

import hashlib
import io
import logging
import shutil
import uuid
import zipfile
from pathlib import Path
from typing import Awaitable, Callable

from automaton.automaton import Action, Automaton, State
from automaton.automaton_builder import AutomatonBuilder
from session import Session

logger = logging.getLogger(__name__)

MODELS_DIR = Path(__file__).parent.parent / "models"
DEFAULT_MODEL_NAME = "default"

# Called with the newly-active Automaton once activate_model()/put_model()
# have committed it.
CommitCallback = Callable[[Automaton], Awaitable[None]]


class ModelService(object):
    def __init__(self, db) -> None:
        self._db = db
        # Pure build cache, not "active" state — see _load_and_validate.
        self._automaton_cache: dict[str, Automaton] = {}
        # model_name -> content hash it was last built from (see
        # _compute_content_hash), kept alongside the automaton cache so
        # the file watcher (model_watcher.py) can tell a genuine on-disk
        # change apart from an event echoing its own upload.
        self._model_hashes: dict[str, str] = {}
        # Fail fast at boot if the active model can't load.
        self.get_active_automaton_and_state()

    @staticmethod
    def _is_safe_model_name(model_name: str) -> bool:
        """No path traversal: must be a single plain path segment — not
        empty, not '.'/'..', no separators, resolving to itself when
        treated as a bare filename."""
        if not model_name or model_name in (".", ".."):
            return False
        return Path(model_name).name == model_name

    def _load_model(self, model_name: str) -> Automaton:
        cached = self._automaton_cache.get(model_name)
        if cached is not None:
            return cached

        if not ModelService._is_safe_model_name(model_name):
            raise ValueError(f"Invalid model name: '{model_name}'.")

        model_dir = MODELS_DIR / model_name
        if not model_dir.is_dir():
            raise ValueError(f"Model '{model_name}' does not exist.")
        automaton = AutomatonBuilder().build(model_dir / "index.yml")
        self._automaton_cache[model_name] = automaton
        self._model_hashes[model_name] = self._compute_content_hash(model_dir)
        return automaton

    @staticmethod
    def _compute_content_hash(model_dir: Path) -> str:
        """Deterministic hash of every non-hidden file under `model_dir`
        (index.yml plus any attachments) — same tree, same hash,
        regardless of filesystem traversal order. Hidden files are
        excluded so the upload path's own temp files/staging dirs never
        affect it."""
        digest = hashlib.sha256()
        files = sorted(p for p in model_dir.rglob("*") if p.is_file() and not p.name.startswith("."))
        for file_path in files:
            digest.update(file_path.relative_to(model_dir).as_posix().encode("utf-8"))
            digest.update(b"\0")
            digest.update(file_path.read_bytes())
            digest.update(b"\0")
        return digest.hexdigest()

    async def _finalize_model_update(
        self, model_name: str, model_dir: Path, automaton: Automaton, commit: CommitCallback
    ) -> bool:
        """Used by the upload path (_put_yaml_model/_put_zip_model): a
        deliberate replace, so it wipes `model_name`'s conversation data
        if it's currently active, before awaiting `commit`."""
        self._automaton_cache[model_name] = automaton
        self._model_hashes[model_name] = self._compute_content_hash(model_dir)
        if model_name == self.get_active_model_name():
            self._db.reset_model(model_name)
            await commit(automaton)
            return True
        return False

    async def _finalize_hot_reload(
        self, model_name: str, model_dir: Path, automaton: Automaton, commit: CommitCallback
    ) -> bool:
        """Used by the file watcher only: refreshes the cache and, if
        `model_name` is active, awaits `commit` — but never wipes
        conversation data (a live edit isn't a deliberate replace). If the
        persisted current state no longer exists (renamed/removed), fixes
        it to init_action.target via one corrective transition instead."""
        self._automaton_cache[model_name] = automaton
        self._model_hashes[model_name] = self._compute_content_hash(model_dir)
        if model_name != self.get_active_model_name():
            return False

        current_state_key = self._db.get_current_state(model_name)
        if current_state_key is not None and current_state_key not in automaton.states:
            target_state = automaton.get_state(automaton.init_action.target)
            logger.warning(
                "Model '%s': persisted state '%s' no longer exists after reload — "
                "resetting to init_action.target '%s' (conversation history kept).",
                model_name, current_state_key, automaton.init_action.target,
            )
            self._db.save_transition(
                current_state_key,
                "model-reloaded",
                automaton.init_action.target,
                model_name,
                transition_log_level=target_state.transition_log_level,
            )

        await commit(automaton)
        return True

    async def refresh_model_from_disk(self, model_name: str, commit: CommitCallback) -> bool | None:
        """For the file watcher only (model_watcher.py): compares
        `model_name`'s current on-disk content hash against the cache
        before doing anything else — this is what tells a genuine external
        edit apart from an event echoing the upload path's own write, or a
        duplicate event for the same logical change (no time-window
        suppression anywhere else). Only a genuine mismatch rebuilds and
        refreshes the cache. Returns None if nothing happened (no change,
        or the model/content turned out invalid), else whether
        `model_name` was the active model (see _finalize_hot_reload)."""
        if not self._is_safe_model_name(model_name):
            return None
        model_dir = MODELS_DIR / model_name
        if not model_dir.is_dir():
            return None

        try:
            current_hash = self._compute_content_hash(model_dir)
        except OSError:
            return None  # file mid-write; a later event will retry

        if current_hash == self._model_hashes.get(model_name):
            return None

        try:
            automaton = AutomatonBuilder().build(model_dir / "index.yml")
        except Exception as exc:
            logger.error("Model watcher: '%s' failed to reload after a file change: %s", model_name, exc)
            return None

        return await self._finalize_hot_reload(model_name, model_dir, automaton, commit)

    @staticmethod
    def _looks_like_zip(content_type: str | None, content: bytes) -> bool:
        """Content-Type decides first ('zip'/'yaml' in the media type); a
        missing or generic header falls back to sniffing the zip magic
        number, unambiguous regardless of what the client claims."""
        if content_type:
            media_type = content_type.split(";")[0].strip().lower()
            if "zip" in media_type:
                return True
            if "yaml" in media_type or "yml" in media_type:
                return False
        return content[:4] == b"PK\x03\x04"

    @staticmethod
    def _extract_zip_safely(content: bytes, staging_dir: Path) -> None:
        """Validates zip-slip safety, flatness, and exactly one root
        'index.yml' — all before extracting anything. Raises ValueError or
        zipfile.BadZipFile on any violation."""
        with zipfile.ZipFile(io.BytesIO(content)) as zf:
            names = [entry.replace("\\", "/") for entry in zf.namelist()]
            staging_resolved = staging_dir.resolve()

            for name in names:
                # Zip-slip protection: mandatory before extracting anything.
                if name.startswith("/") or any(part == ".." for part in Path(name).parts):
                    raise ValueError(f"Unsafe path inside zip: '{name}'.")
                resolved = (staging_dir / name).resolve()
                if resolved != staging_resolved and staging_resolved not in resolved.parents:
                    raise ValueError(f"Unsafe path inside zip: '{name}'.")
                # Flat only: a directory entry or a nested file both contain '/'.
                if "/" in name:
                    raise ValueError(f"Zip must be flat (no subdirectories): found '{name}'.")

            index_entries = [n for n in names if n == "index.yml"]
            other_yaml_entries = [
                n for n in names if n != "index.yml" and n.lower().endswith((".yml", ".yaml"))
            ]
            if not index_entries:
                raise ValueError("Zip must contain an 'index.yml' file at its root.")
            if len(index_entries) > 1:
                raise ValueError("Zip contains more than one 'index.yml'.")
            if other_yaml_entries:
                raise ValueError(
                    "Zip must contain only one YAML file (index.yml) at its root; "
                    f"also found: {', '.join(sorted(other_yaml_entries))}"
                )

            zf.extractall(staging_dir)

    def get_active_model_name(self) -> str:
        """The current session user's active model name, read fresh from
        the DB every time. Defaults to (and persists) "default" the first
        time this user has no Settings row yet."""
        user = Session().user
        model_name = self._db.get_active_model_name(user)
        if model_name is None:
            model_name = DEFAULT_MODEL_NAME
            self._db.set_active_model_name(model_name, user)
        return model_name

    def get_active_automaton_and_state(self) -> tuple[Automaton, State]:
        """The active Automaton paired with its current State — falls back
        to init_action.target if none is persisted yet, or the persisted
        one was renamed/removed on disk since. A pure read, no side
        effect: never returns the reserved implicit state ("") itself, so
        every caller of this (not just ChatService.open_if_needed) always
        sees a real state, whether or not init_action has actually been
        resolved/persisted yet."""
        model_name = self.get_active_model_name()
        automaton = self._load_model(model_name)
        state_key = self._db.get_current_state(model_name)
        if state_key is None or state_key not in automaton.states:
            if state_key is not None:
                logger.warning(
                    "Model '%s': persisted state '%s' no longer exists (renamed/removed on "
                    "disk?) — falling back to init_action.target '%s'.",
                    model_name, state_key, automaton.init_action.target,
                )
            state_key = automaton.init_action.target
        return automaton, automaton.get_state(state_key)

    def apply_manual_action(self, action_name: str) -> tuple[dict, Action, str]:
        """Applies a manual (button) action and returns the destination
        state's payload, the Action that fired, and the source state's
        key (e.g. to detect a self-loop)."""
        automaton, state = self.get_active_automaton_and_state()
        action = automaton.move(state.key, action_name)
        new_state = automaton.get_state(action.target)
        # Always saved, self-loop or not — a real history entry either
        # way. A self-loop just never counts toward history_cutoff's
        # cutoff (see db.get_last_transition_timestamp).
        self._db.save_transition(
            state.key,
            action_name,
            new_state.key,
            self.get_active_model_name(),
            transition_log_level=new_state.transition_log_level,
        )
        return automaton.get_state_payload(new_state), action, state.key

    def get_active_state_payload(self) -> dict:
        automaton, state = self.get_active_automaton_and_state()
        return automaton.get_state_payload(state)

    def reset_active_model(self) -> None:
        self._db.reset_model(self.get_active_model_name())

    def list_models(self) -> dict:
        """Every subdirectory of models/ with an index.yml (unvalidated —
        real validation is at activate/put time). '.'-prefixed dirs are
        staging artifacts, excluded."""
        if not MODELS_DIR.is_dir():
            names = []
        else:
            names = sorted(
                entry.name
                for entry in MODELS_DIR.iterdir()
                if entry.is_dir() and not entry.name.startswith(".") and (entry / "index.yml").is_file()
            )
        return {"models": names, "active": self.get_active_model_name()}

    async def activate_model(self, model_name: str, commit: CommitCallback) -> Automaton:
        """Validates via _load_and_validate(), persists `model_name` as
        active, then awaits `commit(new_automaton)`."""
        new_automaton = self._load_model(model_name)
        self._db.set_active_model_name(model_name, Session().user)
        await commit(new_automaton)
        return new_automaton

    async def activate_model_idempotent(self, model_name: str, commit: CommitCallback) -> Automaton:
        """Always validates `model_name` first, even if already active —
        idempotency only skips the swap + commit, never the correctness
        checks. A different model delegates to activate_model()."""
        new_automaton = self._load_model(model_name)
        if model_name == self.get_active_model_name():
            return new_automaton
        return await self.activate_model(model_name, commit)

    async def _put_yaml_model(self, model_name: str, content: bytes, commit: CommitCallback) -> dict:
        """Writes a temp file inside the model dir so attachment paths
        resolve during validation; renames to index.yml only on success,
        wiping any conversation data `model_name` already had."""
        model_dir = MODELS_DIR / model_name
        dir_preexisted = model_dir.is_dir()
        model_dir.mkdir(parents=True, exist_ok=True)

        temp_path = model_dir / f".tmp_{uuid.uuid4().hex}.yml"
        temp_path.write_bytes(content)
        final_path = model_dir / "index.yml"

        try:
            new_automaton = AutomatonBuilder().build(temp_path)
        except Exception as exc:
            # Any way this file fails to become a usable Automaton is
            # equally "this upload is invalid" to the caller.
            temp_path.unlink(missing_ok=True)
            if not dir_preexisted:
                try:
                    model_dir.rmdir()
                except OSError:
                    pass  # not empty (e.g. a concurrent PUT of the same name) — leave it
            raise ValueError(f"Invalid model definition: {exc}") from exc

        temp_path.replace(final_path)

        self._db.set_active_model_name(model_name, Session().user)
        await self._finalize_model_update(model_name, model_dir, new_automaton, commit)

        return {"success": True, "model_name": model_name}

    async def _put_zip_model(self, model_name: str, content: bytes, commit: CommitCallback) -> dict:
        """Extracts into a temp dir (so attachment paths resolve during
        validation), then promotes it into place with one rename on
        success, wiping any conversation data `model_name` already had."""
        staging_dir = MODELS_DIR / f".tmp_{uuid.uuid4().hex}"
        staging_dir.mkdir(parents=True)

        try:
            self._extract_zip_safely(content, staging_dir)
        except (zipfile.BadZipFile, ValueError) as exc:
            shutil.rmtree(staging_dir, ignore_errors=True)
            raise ValueError(str(exc)) from exc

        index_path = staging_dir / "index.yml"
        final_dir = MODELS_DIR / model_name

        try:
            new_automaton = AutomatonBuilder().build(index_path)
        except Exception as exc:
            shutil.rmtree(staging_dir, ignore_errors=True)
            raise ValueError(f"Invalid model definition: {exc}") from exc

        if final_dir.exists():
            shutil.rmtree(final_dir)
        staging_dir.rename(final_dir)

        self._db.set_active_model_name(model_name, Session().user)
        await self._finalize_model_update(model_name, final_dir, new_automaton, commit)

        return {"success": True, "model_name": model_name}

    async def put_model(
        self, model_name: str, content: bytes, content_type: str | None, commit: CommitCallback
    ) -> dict:
        """Creates or replaces a model from a raw body (YAML or zip, told
        apart by _looks_like_zip). Stages -> validates -> only on success
        commits and swaps the active automaton via `commit`."""
        if not self._is_safe_model_name(model_name):
            raise ValueError(f"Invalid model name: '{model_name}'.")

        if self._looks_like_zip(content_type, content):
            return await self._put_zip_model(model_name, content, commit)
        return await self._put_yaml_model(model_name, content, commit)

    def export_model_zip(self, model_name: str) -> bytes:
        """Exports `model_name` as a zip in the exact layout PUT accepts,
        so it round-trips with no transformation. Not restricted to the
        active model; raises FileNotFoundError if unknown."""
        if not self._is_safe_model_name(model_name) or not (MODELS_DIR / model_name).is_dir():
            raise FileNotFoundError(f"Model '{model_name}' does not exist.")

        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
            for entry in sorted((MODELS_DIR / model_name).iterdir()):
                if entry.is_file() and not entry.name.startswith("."):
                    zf.write(entry, arcname=entry.name)
        return buffer.getvalue()

    @staticmethod
    def _check_editable_file_name(file_name: str) -> None:
        """The only file this endpoint pair (get_model_file/put_model_file)
        will ever read or write — everything else in a model's directory
        (attachments) is out of scope for now."""
        if file_name != "index.yml":
            raise ValueError(f"Unsupported file '{file_name}': only 'index.yml' can be read/edited via this endpoint.")

    def get_model_file(self, model_name: str, file_name: str) -> str:
        """Raw text content of `file_name` (only 'index.yml' for now)
        inside `model_name`'s directory — the read side of
        put_model_file(), round-tripping with no transformation."""
        self._check_editable_file_name(file_name)
        if not self._is_safe_model_name(model_name) or not (MODELS_DIR / model_name).is_dir():
            raise FileNotFoundError(f"Model '{model_name}' does not exist.")
        return (MODELS_DIR / model_name / file_name).read_text(encoding="utf-8")

    async def put_model_file(
        self, model_name: str, file_name: str, content: bytes, commit: CommitCallback
    ) -> dict:
        """Edits `file_name` (only 'index.yml' for now) of an existing
        model in place: stages a full copy of the model's directory
        (attachments included), swaps in the new content, and validates it
        with the same AutomatonBuilder used by every other load path.
        Unlike put_model(), this never creates a new model — 404 if
        `model_name` doesn't already exist — and never force-activates it;
        if it's already active it's refreshed via `commit`, same as
        _finalize_model_update's other callers."""
        self._check_editable_file_name(file_name)
        if not self._is_safe_model_name(model_name) or not (MODELS_DIR / model_name).is_dir():
            raise FileNotFoundError(f"Model '{model_name}' does not exist.")
        model_dir = MODELS_DIR / model_name

        staging_dir = MODELS_DIR / f".tmp_{uuid.uuid4().hex}"
        shutil.copytree(model_dir, staging_dir)
        (staging_dir / file_name).write_bytes(content)

        try:
            new_automaton = AutomatonBuilder().build(staging_dir / file_name)
        except Exception as exc:
            shutil.rmtree(staging_dir, ignore_errors=True)
            raise ValueError(f"Invalid model definition: {exc}") from exc

        shutil.rmtree(model_dir)
        staging_dir.rename(model_dir)

        await self._finalize_model_update(model_name, model_dir, new_automaton, commit)

        return {"success": True, "model_name": model_name}

    async def delete_model(self, model_name: str, commit: CommitCallback) -> None:
        """Removes models/<model_name>/ from disk plus its conversation
        data. Any model, active or not, except "default" (raises
        PermissionError). Reactivates "default" if it was active."""
        if not self._is_safe_model_name(model_name) or not (MODELS_DIR / model_name).is_dir():
            raise FileNotFoundError(f"Model '{model_name}' does not exist.")
        if model_name == DEFAULT_MODEL_NAME:
            raise PermissionError("The default model cannot be deleted.")

        shutil.rmtree(MODELS_DIR / model_name)
        self._db.reset_model(model_name)
        # No orphaned Automaton (or hash) for a model that no longer exists.
        self._automaton_cache.pop(model_name, None)
        self._model_hashes.pop(model_name, None)

        if model_name == self.get_active_model_name():
            await self.activate_model(DEFAULT_MODEL_NAME, commit)
