"""Validating/staging/committing project activations, uploads, and
deletions — plus every db.py access tied to "which project/state is
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

from automaton.automaton import Action, Automaton, State
from automaton.automaton_builder import AutomatonBuilder
from session import Session

logger = logging.getLogger(__name__)

PROJECTS_DIR = Path(__file__).parent.parent / "projects"
DEFAULT_PROJECT_NAME = "default"
# What the file explorer/editor endpoints will read, write, list, or delete —
# index.yml plus the text/plain attachment extensions from
# AutomatonBuilder.EXTENSION_TO_MEDIA_TYPE (binary attachments like .pdf stay
# out of scope for now).
TEXT_EDITABLE_EXTENSIONS = {".yml", ".yaml", ".txt", ".md", ".csv"}

# Called with the newly-active Automaton once activate_project()/put_project()
# have committed it.
CommitCallback = Callable[[Automaton], Awaitable[None]]


class ProjectService(object):
    def __init__(self, db) -> None:
        self._db = db
        # Pure build cache, not "active" state — see _load_and_validate.
        self._automaton_cache: dict[str, Automaton] = {}
        # Fail fast at boot if the active project can't load.
        self.get_active_automaton_and_state()

    @staticmethod
    def _is_safe_project_name(project_name: str) -> bool:
        """No path traversal: must be a single plain path segment — not
        empty, not '.'/'..', no separators, resolving to itself when
        treated as a bare filename."""
        if not project_name or project_name in (".", ".."):
            return False
        return Path(project_name).name == project_name

    def _load_project(self, project_name: str) -> Automaton:
        cached = self._automaton_cache.get(project_name)
        if cached is not None:
            return cached

        if not ProjectService._is_safe_project_name(project_name):
            raise ValueError(f"Invalid project name: '{project_name}'.")

        project_dir = PROJECTS_DIR / project_name
        if not project_dir.is_dir():
            raise ValueError(f"Project '{project_name}' does not exist.")
        automaton = AutomatonBuilder().build(project_dir / "index.yml")
        self._automaton_cache[project_name] = automaton
        return automaton

    async def _finalize_project_update(
        self, project_name: str, automaton: Automaton, commit: CommitCallback
    ) -> bool:
        """Used by the upload path (_put_yaml_project/_put_zip_project/
        put_project_file): a deliberate replace, so it wipes `project_name`'s
        conversation data if it's currently active, before awaiting
        `commit`."""
        self._automaton_cache[project_name] = automaton
        if project_name == self.get_active_project_name():
            self._db.reset_project(project_name)
            await commit(automaton)
            return True
        return False

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

    def get_active_project_name(self) -> str:
        """The current session user's active project name, read fresh from
        the DB every time. Defaults to (and persists) "default" the first
        time this user has no Settings row yet."""
        user = Session().user
        project_name = self._db.get_active_project_name(user)
        if project_name is None:
            project_name = DEFAULT_PROJECT_NAME
            self._db.set_active_project_name(project_name, user)
        return project_name

    def get_active_automaton_and_state(self) -> tuple[Automaton, State]:
        """The active Automaton paired with its current State — falls back
        to init_action.target if none is persisted yet, or the persisted
        one was renamed/removed on disk since. A pure read, no side
        effect: never returns the reserved implicit state ("") itself, so
        every caller of this (not just ChatService.open_if_needed) always
        sees a real state, whether or not init_action has actually been
        resolved/persisted yet."""
        project_name = self.get_active_project_name()
        automaton = self._load_project(project_name)
        state_key = self._db.get_current_state(project_name)
        if state_key is None or state_key not in automaton.states:
            if state_key is not None:
                logger.warning(
                    "Project '%s': persisted state '%s' no longer exists (renamed/removed on "
                    "disk?) — falling back to init_action.target '%s'.",
                    project_name, state_key, automaton.init_action.target,
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
            self.get_active_project_name(),
            transition_log_level=new_state.transition_log_level,
        )
        return automaton.get_state_payload(new_state), action, state.key

    def get_active_state_payload(self) -> dict:
        automaton, state = self.get_active_automaton_and_state()
        return automaton.get_state_payload(state)

    def reset_active_project(self) -> None:
        self._db.reset_project(self.get_active_project_name())

    def get_project_signals(self, project_name: str) -> list[dict]:
        """Signal definitions (name/ui_label/description/attachments) of
        `project_name`'s last successfully saved index.yml — the source for
        the "Edit project" view's Inspect panel. Reads through
        _load_project's cache, which every mutating path (put_project/
        put_project_file/delete_project_file, via _finalize_project_update)
        keeps fresh as of its own last successful save, regardless of which
        project is currently active — so this never reflects an
        in-progress unsaved edit."""
        automaton = self._load_project(project_name)
        return [
            {
                "name": signal.name,
                "ui_label": signal.ui_label,
                "description": signal.description,
                "attachments": [a.filename for a in signal.attachments],
            }
            for signal in automaton.signals
        ]

    def get_project_graph(self, project_name: str) -> dict:
        """The project's state machine as nodes (states) and edges
        (actions) — the source for the "Edit project" view's Inspect panel
        graph (rendered client-side with Cytoscape). Reads through the same
        _load_project cache as get_project_signals, so it reflects the last
        successfully saved index.yml, not any in-progress unsaved edit. The
        reserved implicit state ("", see AutomatonBuilder.build) is never a
        real state and is excluded; its target (automaton.init_action.target)
        is exposed as each node's `is_start` flag instead of a synthetic
        edge from nowhere."""
        automaton = self._load_project(project_name)
        real_states = [state for state in automaton.states.values() if state.key != ""]
        nodes = [
            {
                "key": state.key,
                "label": state.label,
                "description": state.description,
                "final": state.final,
                "is_start": state.key == automaton.init_action.target,
                "chat": state.chat,
                "on_enter": state.on_enter,
                "history_cutoff": state.history_cutoff,
                "transition_log_level": state.transition_log_level,
                "attachments": [a.filename for a in state.attachments],
            }
            for state in real_states
        ]
        edges = [
            {
                "source": state.key,
                "target": action.target,
                "action_name": action.name,
                "label": action.label,
                "button_text": action.button_text,
                "trigger": action.trigger,
                "has_trigger": action.trigger is not None,
                "action_prompt": action.action_prompt,
            }
            for state in real_states
            for action in state.actions
        ]
        return {"nodes": nodes, "edges": edges}

    def list_projects(self) -> dict:
        """Every subdirectory of projects/ with an index.yml (unvalidated —
        real validation is at activate/put time). '.'-prefixed dirs are
        staging artifacts, excluded."""
        if not PROJECTS_DIR.is_dir():
            names = []
        else:
            names = sorted(
                entry.name
                for entry in PROJECTS_DIR.iterdir()
                if entry.is_dir() and not entry.name.startswith(".") and (entry / "index.yml").is_file()
            )
        return {"projects": names, "active": self.get_active_project_name()}

    async def activate_project(self, project_name: str, commit: CommitCallback) -> Automaton:
        """Validates via _load_and_validate(), persists `project_name` as
        active, then awaits `commit(new_automaton)`."""
        new_automaton = self._load_project(project_name)
        self._db.set_active_project_name(project_name, Session().user)
        await commit(new_automaton)
        return new_automaton

    async def activate_project_idempotent(self, project_name: str, commit: CommitCallback) -> Automaton:
        """Always validates `project_name` first, even if already active —
        idempotency only skips the swap + commit, never the correctness
        checks. A different project delegates to activate_project()."""
        new_automaton = self._load_project(project_name)
        if project_name == self.get_active_project_name():
            return new_automaton
        return await self.activate_project(project_name, commit)

    async def _put_yaml_project(self, project_name: str, content: bytes, commit: CommitCallback) -> dict:
        """Writes a temp file inside the project dir so attachment paths
        resolve during validation; renames to index.yml only on success,
        wiping any conversation data `project_name` already had."""
        project_dir = PROJECTS_DIR / project_name
        dir_preexisted = project_dir.is_dir()
        project_dir.mkdir(parents=True, exist_ok=True)

        temp_path = project_dir / f".tmp_{uuid.uuid4().hex}.yml"
        temp_path.write_bytes(content)
        final_path = project_dir / "index.yml"

        try:
            new_automaton = AutomatonBuilder().build(temp_path)
        except Exception as exc:
            # Any way this file fails to become a usable Automaton is
            # equally "this upload is invalid" to the caller.
            temp_path.unlink(missing_ok=True)
            if not dir_preexisted:
                try:
                    project_dir.rmdir()
                except OSError:
                    pass  # not empty (e.g. a concurrent PUT of the same name) — leave it
            raise ValueError(f"Invalid project definition: {exc}") from exc

        temp_path.replace(final_path)

        self._db.set_active_project_name(project_name, Session().user)
        await self._finalize_project_update(project_name, new_automaton, commit)

        return {"success": True, "project_name": project_name}

    async def _put_zip_project(self, project_name: str, content: bytes, commit: CommitCallback) -> dict:
        """Extracts into a temp dir (so attachment paths resolve during
        validation), then promotes it into place with one rename on
        success, wiping any conversation data `project_name` already had."""
        staging_dir = PROJECTS_DIR / f".tmp_{uuid.uuid4().hex}"
        staging_dir.mkdir(parents=True)

        try:
            self._extract_zip_safely(content, staging_dir)
        except (zipfile.BadZipFile, ValueError) as exc:
            shutil.rmtree(staging_dir, ignore_errors=True)
            raise ValueError(str(exc)) from exc

        index_path = staging_dir / "index.yml"
        final_dir = PROJECTS_DIR / project_name

        try:
            new_automaton = AutomatonBuilder().build(index_path)
        except Exception as exc:
            shutil.rmtree(staging_dir, ignore_errors=True)
            raise ValueError(f"Invalid project definition: {exc}") from exc

        if final_dir.exists():
            shutil.rmtree(final_dir)
        staging_dir.rename(final_dir)

        self._db.set_active_project_name(project_name, Session().user)
        await self._finalize_project_update(project_name, new_automaton, commit)

        return {"success": True, "project_name": project_name}

    async def put_project(
        self, project_name: str, content: bytes, content_type: str | None, commit: CommitCallback
    ) -> dict:
        """Creates or replaces a project from a raw body (YAML or zip, told
        apart by _looks_like_zip). Stages -> validates -> only on success
        commits and swaps the active automaton via `commit`."""
        if not self._is_safe_project_name(project_name):
            raise ValueError(f"Invalid project name: '{project_name}'.")

        if self._looks_like_zip(content_type, content):
            return await self._put_zip_project(project_name, content, commit)
        return await self._put_yaml_project(project_name, content, commit)

    def export_project_zip(self, project_name: str) -> bytes:
        """Exports `project_name` as a zip in the exact layout PUT accepts,
        so it round-trips with no transformation. Not restricted to the
        active project; raises FileNotFoundError if unknown."""
        if not self._is_safe_project_name(project_name) or not (PROJECTS_DIR / project_name).is_dir():
            raise FileNotFoundError(f"Project '{project_name}' does not exist.")

        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
            for entry in sorted((PROJECTS_DIR / project_name).iterdir()):
                if entry.is_file() and not entry.name.startswith("."):
                    zf.write(entry, arcname=entry.name)
        return buffer.getvalue()

    @staticmethod
    def _check_editable_file_name(file_name: str) -> None:
        """Everything the file explorer/editor endpoints (list_project_files/
        get_project_file/put_project_file/delete_project_file) will read,
        write, or delete: a flat, non-hidden file name (no path traversal)
        with one of TEXT_EDITABLE_EXTENSIONS — index.yml plus its text
        attachments. Binary attachments and anything else in a project's
        directory stay out of scope."""
        if not file_name or file_name in (".", "..") or Path(file_name).name != file_name:
            raise ValueError(f"Invalid file name: '{file_name}'.")
        if file_name.startswith("."):
            raise ValueError(f"Invalid file name: '{file_name}'.")
        extension = Path(file_name).suffix.lower()
        if extension not in TEXT_EDITABLE_EXTENSIONS:
            raise ValueError(
                f"Unsupported file '{file_name}': only {sorted(TEXT_EDITABLE_EXTENSIONS)} "
                "files can be read/edited via this endpoint."
            )

    def list_project_files(self, project_name: str) -> list[str]:
        """Every text-editable file directly inside `project_name`'s
        directory (index.yml plus any text attachments) — the source list
        for the "Edit project" view's file explorer panel. index.yml sorts
        first, then the rest alphabetically."""
        if not self._is_safe_project_name(project_name) or not (PROJECTS_DIR / project_name).is_dir():
            raise FileNotFoundError(f"Project '{project_name}' does not exist.")
        names = [
            entry.name
            for entry in (PROJECTS_DIR / project_name).iterdir()
            if entry.is_file() and not entry.name.startswith(".") and entry.suffix.lower() in TEXT_EDITABLE_EXTENSIONS
        ]
        names.sort(key=lambda name: (name != "index.yml", name))
        return names

    def get_project_file(self, project_name: str, file_name: str) -> str:
        """Raw text content of `file_name` (index.yml or a text attachment)
        inside `project_name`'s directory — the read side of
        put_project_file(), round-tripping with no transformation."""
        self._check_editable_file_name(file_name)
        if not self._is_safe_project_name(project_name) or not (PROJECTS_DIR / project_name).is_dir():
            raise FileNotFoundError(f"Project '{project_name}' does not exist.")
        full_path = PROJECTS_DIR / project_name / file_name
        if not full_path.is_file():
            raise FileNotFoundError(f"File '{file_name}' does not exist in project '{project_name}'.")
        return full_path.read_text(encoding="utf-8")

    async def put_project_file(
        self, project_name: str, file_name: str, content: bytes, commit: CommitCallback
    ) -> dict:
        """Creates or edits `file_name` (index.yml or a text attachment) of
        an existing project in place: stages a full copy of the project's
        directory, swaps in the new content, and validates the result by
        rebuilding from the staged index.yml (the definition file — always
        that one, even when `file_name` is an attachment, since editing an
        attachment can only be validated through the yaml that references
        it) with the same AutomatonBuilder used by every other load path.
        Unlike put_project(), this never creates a new project — 404 if
        `project_name` doesn't already exist — and never force-activates it;
        if it's already active it's refreshed via `commit`, same as
        _finalize_project_update's other callers."""
        self._check_editable_file_name(file_name)
        if not self._is_safe_project_name(project_name) or not (PROJECTS_DIR / project_name).is_dir():
            raise FileNotFoundError(f"Project '{project_name}' does not exist.")
        project_dir = PROJECTS_DIR / project_name

        staging_dir = PROJECTS_DIR / f".tmp_{uuid.uuid4().hex}"
        shutil.copytree(project_dir, staging_dir)
        (staging_dir / file_name).write_bytes(content)

        try:
            new_automaton = AutomatonBuilder().build(staging_dir / "index.yml")
        except Exception as exc:
            shutil.rmtree(staging_dir, ignore_errors=True)
            raise ValueError(f"Invalid project definition: {exc}") from exc

        shutil.rmtree(project_dir)
        staging_dir.rename(project_dir)

        await self._finalize_project_update(project_name, new_automaton, commit)

        return {"success": True, "project_name": project_name}

    async def delete_project_file(
        self, project_name: str, file_name: str, commit: CommitCallback
    ) -> None:
        """Deletes one text attachment from `project_name`'s directory —
        never index.yml itself (raises PermissionError). Stages a copy,
        removes the file, and re-validates via AutomatonBuilder before
        committing, so deleting a file still referenced as an attachment is
        rejected instead of silently breaking the project."""
        self._check_editable_file_name(file_name)
        if file_name == "index.yml":
            raise PermissionError("index.yml cannot be deleted.")
        if not self._is_safe_project_name(project_name) or not (PROJECTS_DIR / project_name).is_dir():
            raise FileNotFoundError(f"Project '{project_name}' does not exist.")
        project_dir = PROJECTS_DIR / project_name
        if not (project_dir / file_name).is_file():
            raise FileNotFoundError(f"File '{file_name}' does not exist in project '{project_name}'.")

        staging_dir = PROJECTS_DIR / f".tmp_{uuid.uuid4().hex}"
        shutil.copytree(project_dir, staging_dir)
        (staging_dir / file_name).unlink()

        try:
            new_automaton = AutomatonBuilder().build(staging_dir / "index.yml")
        except Exception as exc:
            shutil.rmtree(staging_dir, ignore_errors=True)
            raise ValueError(f"Invalid project definition: {exc}") from exc

        shutil.rmtree(project_dir)
        staging_dir.rename(project_dir)

        await self._finalize_project_update(project_name, new_automaton, commit)

    async def delete_project(self, project_name: str, commit: CommitCallback) -> None:
        """Removes projects/<project_name>/ from disk plus its conversation
        data. Any project, active or not, except "default" (raises
        PermissionError). Reactivates "default" if it was active."""
        if not self._is_safe_project_name(project_name) or not (PROJECTS_DIR / project_name).is_dir():
            raise FileNotFoundError(f"Project '{project_name}' does not exist.")
        if project_name == DEFAULT_PROJECT_NAME:
            raise PermissionError("The default project cannot be deleted.")

        shutil.rmtree(PROJECTS_DIR / project_name)
        self._db.reset_project(project_name)
        # No orphaned Automaton for a project that no longer exists.
        self._automaton_cache.pop(project_name, None)

        if project_name == self.get_active_project_name():
            await self.activate_project(DEFAULT_PROJECT_NAME, commit)
