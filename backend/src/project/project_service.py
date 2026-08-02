"""Validating/staging/committing project activations, uploads, and
deletions — plus every db.py access tied to "which project/state is
active", encapsulated here so other layers never reach into db.py
themselves for that.
"""
from __future__ import annotations

import io
import logging
import zipfile
import tempfile
from pathlib import Path
from typing import Awaitable, Callable

from automaton.automaton import Action, Automaton, State, StatePayload
from automaton.automaton_builder import AutomatonBuilder
from session import Session
from db import Db

logger = logging.getLogger(__name__)

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
    def __init__(self, db: Db) -> None:
        self._db = db
        self._automaton_cache: dict[str, Automaton] = {}

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

        archives = self._db.get_archives(project_name)

        if not archives:
            raise  FileNotFoundError(f"Project '{project_name}' does not exist.")
        if 'index.yml' not in archives:
            raise  FileNotFoundError(f"Project '{project_name}' does not contain 'index.yml'.")
        
        automaton = AutomatonBuilder().build(archives)
        self._automaton_cache[project_name] = automaton
        return automaton

    def _project_update_changed(self, existing: dict[str, str], files: dict[str, str]) -> bool:
        """Whether `files` (new content for some subset of a project's
        own files — see _prepare_project_update) is a genuine change
        against `existing`."""
        return any(existing.get(name, "") != content for name, content in files.items())

    def _prepare_project_update(self, project_name: str, files: dict[str, str]) -> tuple[Automaton, dict[str, str] | None]:
        """Builds+validates the Automaton for `files` (new content for
        some subset of `project_name`'s own files — all of them for a
        zip upload, just one for a single-file edit) merged onto its
        current ones. Read-only — never writes anything. Returns
        (automaton, to_persist): the second element is the full merged
        file set to hand to db.save_project_version, or None when nothing
        actually changed (see _project_update_changed) — the caller's
        signal to skip persistence (and, likely, resetting the active
        conversation) entirely."""
        existing = self._db.get_archives(project_name)
        merged = {**existing, **files}

        automaton = AutomatonBuilder().build(merged)

        if not self._project_update_changed(existing, files):
            return automaton, None
        return automaton, merged

    def _file_version_info(self, project_name: str, file_name: str) -> dict:
        result = self._db.get_archive(project_name, file_name)
        if result is None:
            raise FileNotFoundError(f"File '{file_name}' does not exist in project '{project_name}'.")
        content, version = result
        return {
            "content": content,
            "version": version,
            "total_versions": self._db.count_archive_versions(project_name, file_name),
        }

    async def _finalize_project_update(
        self, project_name: str, automaton: Automaton, commit: CommitCallback
    ) -> bool:
        """Used by every project-mutating path (put_project/put_project_file/
        delete_project_file): a deliberate replace, so it wipes `project_name`'s
        conversation data if it's currently active, before awaiting
        `commit`. Archive persistence itself is each caller's own
        responsibility (see db.save_project_version/db.delete_archive) —
        this only refreshes the in-memory automaton cache and, if
        `project_name` is the active project, resets its conversation."""

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

    def apply_manual_action(self, action_name: str, session_id: int) -> tuple[StatePayload, Action, str]:
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
            session_id,
            transition_log_level=new_state.transition_log_level,
        )
        return automaton.get_state_payload(new_state), action, state.key

    def get_active_state_payload(self) -> StatePayload:
        automaton, state = self.get_active_automaton_and_state()
        return automaton.get_state_payload(state)

    def reset_active_project(self) -> None:
        # User-scoped (see db.reset_project_for_user): only the current
        # user's own sessions/messages/signals for this project are wiped
        # — not every user's, unlike delete_project's full reset_project.
        self._db.reset_project_for_user(Session().user, self.get_active_project_name())

    def get_project_signals(self, project_name: str) -> list[dict]:
        """Signal definitions (name/ui_label/ui_description/attachments) of
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
                "ui_description": signal.ui_description,
                "attachments": [a.filename for a in signal.attachments.values()],
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
        real state and is excluded from `nodes` — each real node's own
        `is_start` flag (state.key == automaton.init_action.target) is
        what marks the actual starting state instead. `edges`, unlike
        `nodes`, is built over *every* state including "" — its own single
        action is exactly init_action (see AutomatonBuilder._build_init_action/
        build), so this naturally includes one `source: ""` edge, the
        automaton's own "arrow from nowhere" into its start state. The
        frontend (InspectorGraphTab.vue) renders that edge's source as a
        transparent pseudo-node, same convention "" already has everywhere
        else (Signals.old_state, benchmarkTimeline.js's own synthetic
        session-start entry) for "there was no real prior state"."""
        automaton = self._load_project(project_name)
        real_states = [state for state in automaton.states.values() if state.key != ""]
        nodes = [
            {
                "key": state.key,
                "ui_label": state.ui_label,
                "ui_description": state.ui_description,
                "final": state.final,
                "is_start": state.key == automaton.init_action.target,
                "chat": state.chat,
                "on_enter": state.on_enter,
                "history_cutoff": state.history_cutoff,
                "transition_log_level": state.transition_log_level,
                "attachments": list(state.attachments.keys()),
            }
            for state in real_states
        ]
        edges = [
            {
                "source": state.key,
                "target": action.target,
                "action_name": action.name,
                "ui_label": action.ui_label,
                "ui_description": action.ui_description,
                "ui_button": action.ui_button,
                "trigger": action.trigger,
                "has_trigger": action.trigger is not None,
                "action_prompt": action.action_prompt,
            }
            for state in automaton.states.values()
            for action in state.actions
        ]
        return {"nodes": nodes, "edges": edges}

    def list_projects(self) -> dict:
        names = self._db.list_projects()
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

    async def put_project(
        self, project_name: str, content: bytes, content_type: str | None, commit: CommitCallback
    ) -> dict:

        if not self._looks_like_zip(content_type, content):
            raise ValueError(f"Uploaded file must be zip")

        if not self._is_safe_project_name(project_name):
            raise ValueError(f"Invalid project name: '{project_name}'.")

        with tempfile.TemporaryDirectory() as tmp:
            try:
                staging_dir = Path(tmp)
                self._extract_zip_safely(content, staging_dir)
                files = {
                    file.name: file.read_text()
                    for file in staging_dir.iterdir()
                }
                new_automaton, to_persist = self._prepare_project_update(project_name, files)
            except (zipfile.BadZipFile, ValueError) as exc:
                raise ValueError(str(exc)) from exc
            except Exception as exc:
                logger.exception(exc)
                raise ValueError(f"Invalid project definition: {exc}") from exc

        self._db.set_active_project_name(project_name, Session().user)
        if to_persist is not None:
            self._db.save_project_version(project_name, to_persist)
        await self._finalize_project_update(project_name, new_automaton, commit)

        return {"success": True, "project_name": project_name}

    def export_project_zip(self, project_name: str) -> bytes:
        archives = self._db.get_archives(project_name)
        if archives is None:
            raise FileNotFoundError(f"Project '{project_name}' does not exist.")

        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
            for archive_name, archive_content in archives.items():
                zf.writestr(archive_name, archive_content)

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

        names = self._db.list_archives(project_name)
        names.sort(key=lambda name: (name != "index.yml", name))
        logger.critical(names)
        return names

    def get_project_file(self, project_name: str, file_name: str) -> dict:
        """{content, version, total_versions} for `file_name`'s latest
        version — version/total_versions are what the "Edit project"
        view's Undo/Redo buttons use to know their own enabled range
        (see get_project_file_at_version for a specific past version)."""
        return self._file_version_info(project_name, file_name)

    def get_project_file_at_version(self, project_name: str, file_name: str, version: int) -> dict:
        """Same shape as get_project_file, but for exactly `version` —
        404s (via FileNotFoundError) unless that precise version was
        actually saved for this file."""
        result = self._db.get_archive(project_name, file_name, version=version)
        if result is None:
            raise FileNotFoundError(
                f"File '{file_name}' in project '{project_name}' has no version {version}."
            )
        content, actual_version = result
        return {
            "content": content,
            "version": actual_version,
            "total_versions": self._db.count_archive_versions(project_name, file_name),
        }

    def count_project_file_versions(self, project_name: str, file_name: str) -> int:
        return self._db.count_archive_versions(project_name, file_name)

    def prune_project_history(self, project_name: str) -> None:
        """Discards every file's older versions for `project_name`,
        keeping only each one's current/latest — see db.prune_archive_history."""
        if project_name not in self._db.list_projects():
            raise FileNotFoundError(f"Project '{project_name}' does not exist.")
        self._db.prune_archive_history(project_name)

    async def put_project_file(
        self, project_name: str, file_name: str, content: bytes, commit: CommitCallback
    ) -> dict:

        if project_name not in self._db.list_projects():
            raise FileNotFoundError(f"Project '{project_name}' does not exist.")

        text_content = content.decode("utf-8") if isinstance(content, bytes) else content
        self._check_editable_file_name(file_name)

        try:
            new_automaton, to_persist = self._prepare_project_update(project_name, {file_name: text_content})
        except Exception as exc:
            raise ValueError(f"Invalid project update: {exc}") from exc

        if to_persist is not None:
            self._db.save_project_version(project_name, to_persist)
        await self._finalize_project_update(project_name, new_automaton, commit)

        info = self._file_version_info(project_name, file_name)
        return {"success": True, "project_name": project_name, **info}

    async def delete_project_file(
        self, project_name: str, file_name: str, commit: CommitCallback
    ) -> None:

        if project_name not in self._db.list_projects():
            raise FileNotFoundError(f"Project '{project_name}' does not exist.")

        try:
            archives = self._db.get_archives(project_name=project_name)
            del archives[file_name]
            new_automaton = AutomatonBuilder().build(archives)
        except Exception as exc:
            raise ValueError(f"Invalid project definition: {exc}") from exc

        self._db.delete_archive(project_name, file_name)
        await self._finalize_project_update(project_name, new_automaton, commit)

    async def delete_project(self, project_name: str, commit: CommitCallback) -> None:

        if project_name == DEFAULT_PROJECT_NAME:
            raise PermissionError("The default project cannot be deleted.")

        self._db.reset_project(project_name)
        self._db.delete_archives(project_name)
        self._automaton_cache.pop(project_name, None)

        if project_name == self.get_active_project_name():
            # DEFAULT_PROJECT_NAME is just a reserved name (see above) —
            # not guaranteed to actually exist as an uploaded project (e.g.
            # a fresh install, or it was never uploaded), so activating it
            # blindly can 404. Prefer it when it exists, for continuity;
            # otherwise fall back to whatever project is left, if any —
            # activating nothing (rather than raising) when the deleted
            # project was the last one.
            remaining = self._db.list_projects()
            fallback = DEFAULT_PROJECT_NAME if DEFAULT_PROJECT_NAME in remaining else next(iter(remaining), None)
            if fallback is not None:
                await self.activate_project(fallback, commit)
