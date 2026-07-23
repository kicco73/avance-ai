"""Which automaton is active, plus validating/staging/committing model
activations, uploads, and deletions. `ModelsManager` holds that state as
instance attributes; `models_manager` below is the one shared instance.
"""
from __future__ import annotations

import io
import shutil
import uuid
import zipfile
from pathlib import Path
from typing import Awaitable, Callable

from automaton import Automaton, AutomatonBuilder
from db import db

MODELS_DIR = Path(__file__).parent / "models"
DEFAULT_MODEL_NAME = "default"

# Called with the newly-active Automaton once activate_model()/put_model()
# have committed it — see module docstring.
CommitCallback = Callable[[Automaton], Awaitable[None]]


class ModelsManager(object):
    def __init__(self) -> None:
        self._active_automaton: Automaton | None = None
        self._active_model_name: str | None = None
        self.__init_model()

    @staticmethod
    def _is_safe_model_name(model_name: str) -> bool:
        """No path traversal: must be a single plain path segment — not
        empty, not '.'/'..', no separators, resolving to itself when
        treated as a bare filename."""
        if not model_name or model_name in (".", ".."):
            return False
        return Path(model_name).name == model_name

    @staticmethod
    def _load_and_validate(model_name: str) -> Automaton:
        """Path safety, then that `model_name` exists, then
        AutomatonBuilder.build() — raising ValueError on any failure.
        Shared by every method below that needs to load a model."""
        if not ModelsManager._is_safe_model_name(model_name):
            raise ValueError(f"Invalid model name: '{model_name}'.")
        model_dir = MODELS_DIR / model_name
        if not model_dir.is_dir():
            raise ValueError(f"Model '{model_name}' does not exist.")
        return AutomatonBuilder().build(model_dir / "index.yml")

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

    def _set_active(self, model_name: str, automaton: Automaton) -> None:
        """The one place both pieces of "which model is active" (the
        automaton and its name) update together — every swap site goes
        through this, so they can never drift apart."""
        self._active_automaton = automaton
        self._active_model_name = model_name

    def __init_model(self) -> Automaton:
        """Loads whichever model is persisted as active (Settings table,
        "default" on the very first boot) and restores its current state.
        On failure, falls back to "default" with a full reset; if that
        fails too, propagates so the server fails to start."""
        model_name = db.get_active_model_name()
        if model_name is None:
            model_name = DEFAULT_MODEL_NAME
            db.set_active_model_name(model_name)

        try:
            automaton = self._load_and_validate(model_name)
        except ValueError:
            if model_name == DEFAULT_MODEL_NAME:
                raise
            model_name = DEFAULT_MODEL_NAME
            automaton = self._load_and_validate(model_name)
            db.reset_all()
            db.set_active_model_name(model_name)
        else:
            automaton.set_current_state(db.get_current_state(automaton.initial_state))

        self._set_active(model_name, automaton)
        return automaton

    def get_active_automaton(self) -> Automaton:
        return self._active_automaton

    def get_active_model_name(self) -> str | None:
        return self._active_model_name

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
        return {"models": names, "active": self._active_model_name}

    async def activate_model(self, model_name: str, commit: CommitCallback) -> Automaton:
        """Validates via _load_and_validate(), then unconditionally swaps
        the active automaton and awaits `commit(new_automaton)`. Used by
        delete_model()'s fallback and activate_model_idempotent()."""
        new_automaton = self._load_and_validate(model_name)

        self._set_active(model_name, new_automaton)
        await commit(new_automaton)
        return new_automaton

    async def activate_model_idempotent(self, model_name: str, commit: CommitCallback) -> Automaton:
        """Always validates `model_name` first, even if already active —
        idempotency only skips the swap + conversation reset, never the
        correctness checks. A different model delegates to activate_model()."""
        new_automaton = self._load_and_validate(model_name)
        if model_name == self._active_model_name:
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
            # Broad on purpose: any way this file fails to become a usable
            # Automaton is equally "this upload is invalid" to the caller.
            temp_path.unlink(missing_ok=True)
            if not dir_preexisted:
                try:
                    model_dir.rmdir()
                except OSError:
                    pass  # not empty (e.g. a concurrent PUT of the same name) — leave it
            raise ValueError(f"Invalid model definition: {exc}") from exc

        temp_path.replace(final_path)

        self._set_active(model_name, new_automaton)
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

        self._set_active(model_name, new_automaton)
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
        """Removes models/<model_name>/ from disk — any model, active or
        not (except "default", which raises PermissionError). If it was
        active, reactivates "default" via activate_model()."""
        if not self._is_safe_model_name(model_name) or not (MODELS_DIR / model_name).is_dir():
            raise FileNotFoundError(f"Model '{model_name}' does not exist.")
        if model_name == DEFAULT_MODEL_NAME:
            raise PermissionError("The default model cannot be deleted.")

        shutil.rmtree(MODELS_DIR / model_name)

        if model_name == self._active_model_name:
            await self.activate_model(DEFAULT_MODEL_NAME, commit)


# The one shared instance every other module imports (`from models_manager
# import models_manager`) — see module docstring.
models_manager = ModelsManager()
