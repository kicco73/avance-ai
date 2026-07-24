"""Validating/staging/committing model activations, uploads, and
deletions — plus every db.py access tied to "which model/state is
active", encapsulated here so other layers never reach into db.py
themselves for that.
"""
from __future__ import annotations

import io
import logging
import shutil
import uuid
import zipfile
from pathlib import Path
from typing import Awaitable, Callable

from automaton.automaton import Automaton
from automaton.automaton_builder import AutomatonBuilder
from session import Session

logger = logging.getLogger(__name__)

MODELS_DIR = Path(__file__).parent / "models"
DEFAULT_MODEL_NAME = "default"

# Called with the newly-active Automaton once activate_model()/put_model()
# have committed it.
CommitCallback = Callable[[Automaton], Awaitable[None]]


class ModelService(object):
    def __init__(self, db) -> None:
        self._db = db
        # Pure build cache, not "active" state — see _load_and_validate.
        self._automaton_cache: dict[str, Automaton] = {}
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
        return automaton

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

    def get_active_automaton_and_state(self) -> tuple[Automaton, str]:
        """The active Automaton paired with the state key it's currently
        in (or initial_state, if none persisted yet). Raises ValueError
        if the active model itself fails to load — no silent fallback."""
        model_name = self.get_active_model_name()
        automaton = self._load_model(model_name)
        state_key = self._db.get_current_state(model_name) or automaton.initial_state
        return automaton, state_key

    def apply_manual_action(self, action_name: str) -> dict:
        """Applies a manual (button) action on the active automaton,
        persists the transition, and returns the resulting state payload."""
        automaton, from_state = self.get_active_automaton_and_state()
        new_state = automaton.move(from_state, action_name).target
        self._db.save_transition(
            from_state,
            action_name,
            new_state,
            self.get_active_model_name(),
            transition_log_level=automaton.get_state(new_state).transition_log_level,
        )
        return automaton.get_state_payload(new_state)

    def get_active_state_payload(self) -> dict:
        automaton, state_key = self.get_active_automaton_and_state()
        return automaton.get_state_payload(state_key)

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
        resolve during validation; renames to index.yml only on success.
        Failure leaves the directory exactly as it was."""
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

        # Always built fresh above — refresh the cache entry too.
        self._automaton_cache[model_name] = new_automaton
        self._db.set_active_model_name(model_name, Session().user)
        await commit(new_automaton)

        return {"success": True, "model_name": model_name}

    async def _put_zip_model(self, model_name: str, content: bytes, commit: CommitCallback) -> dict:
        """Extracts into a temp dir (so attachment paths resolve during
        validation), then promotes it into place with one rename on
        success, replacing any previous model of that name."""
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

        # Always built fresh above — refresh the cache entry too.
        self._automaton_cache[model_name] = new_automaton
        self._db.set_active_model_name(model_name, Session().user)
        await commit(new_automaton)

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
        # No orphaned Automaton for a model that no longer exists.
        self._automaton_cache.pop(model_name, None)

        if model_name == self.get_active_model_name():
            await self.activate_model(DEFAULT_MODEL_NAME, commit)
