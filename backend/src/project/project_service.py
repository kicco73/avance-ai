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

from automaton.automaton import Action, ActionPayload, Automaton, SignalPayload, State, StatePayload
from automaton.automaton_builder import AutomatonBuilder
from automaton.automaton_yaml_editor import AutomatonYamlEditor, InitActionTargetError
from session import Session
from db import Db

logger = logging.getLogger(__name__)

# What the file explorer/editor endpoints will read, write, list, or delete —
# index.yml plus the text/plain attachment extensions from
# AutomatonBuilder.EXTENSION_TO_MEDIA_TYPE (binary attachments like .pdf stay
# out of scope for now).
TEXT_EDITABLE_EXTENSIONS = {".yml", ".yaml", ".txt", ".md", ".csv"}

# Called with the newly-active Automaton once activate_project()/put_project()
# have committed it.
CommitCallback = Callable[[Automaton], Awaitable[None]]

# "New project" (see controller.py's POST /api/projects/new) starts from
# this exact sample zip, exactly as if a user had picked it in the
# upload file dialog — same repo-relative layout as every other file
# this module resolves off its own location (see e.g. controller.py's
# own DOCS_DIR), not the process's current working directory. backend/
# samples/ ships alongside backend/src/ in every deployment (see the
# repo's own Dockerfile: `COPY . .` copies the whole repo before src/
# ever runs), so this never depends on the dev checkout specifically.
NEW_PROJECT_TEMPLATE = Path(__file__).resolve().parents[2] / "samples" / "Hello world.zip"
NEW_PROJECT_NAME = "Hello world"


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
        file set (put_project hands it straight to db.save_project_files;
        put_project_file only uses its presence as a changed/unchanged
        signal, since it persists just its own single file via
        db.save_project_file), or None when nothing actually changed (see
        _project_update_changed) — the caller's signal to skip
        persistence (and, likely, resetting the active conversation)
        entirely."""
        existing = self._db.get_archives(project_name)
        merged = {**existing, **files}

        automaton = AutomatonBuilder().build(merged)

        if not self._project_update_changed(existing, files):
            return automaton, None
        return automaton, merged

    def _file_undo_redo_info(self, project_name: str, file_name: str) -> dict:
        content = self._db.get_archive(project_name, file_name)
        if content is None:
            raise FileNotFoundError(f"File '{file_name}' does not exist in project '{project_name}'.")
        user = Session().user
        return {
            "content": content,
            "can_undo": self._db.has_undo(user, project_name, file_name),
            "can_redo": self._db.has_redo(user, project_name, file_name),
        }

    async def _finalize_project_update(
        self, project_name: str, automaton: Automaton, commit: CommitCallback
    ) -> bool:
        """Used by every project-mutating path (put_project/put_project_file/
        undo_project_file/redo_project_file/delete_project_file), before
        awaiting `commit`. Archive persistence itself is each caller's own
        responsibility (see db.save_project_files/db.save_project_file/
        db.delete_archive) — this only refreshes the
        in-memory automaton cache and, if `project_name` is the active
        project, reconciles its live conversation against whatever just
        changed: wiped only if the state it was actually in no longer
        exists in the new definition (a rename/removal genuinely leaves it
        nowhere valid to resume from) — an edit that leaves that one state
        untouched (a wording tweak, a different state entirely, a new
        state/action added alongside it) lets the conversation carry on
        exactly where it was, rather than restarting on every single save
        regardless of whether anything relevant to it actually changed."""

        self._automaton_cache[project_name] = automaton
        if project_name == self.get_active_project_name():
            current_state_key = self._db.get_current_state(project_name)
            if current_state_key is None or current_state_key not in automaton.states:
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
        the DB every time — None if this user has no Settings row yet
        (never activated anything), or their last-active project has since
        been deleted (no project name is reserved/protected from deletion,
        this one included) and nothing else was left to fall back to (see
        delete_project). Every caller that resolves an active *automaton*
        from this (get_active_automaton_and_state, and everything built on
        it) already degrades gracefully when there's genuinely nothing
        active — see GET /api/state's own bare except."""
        name = self._db.get_active_project_name(Session().user)
        if name is None:
            raise FileNotFoundError("No project is currently active.")
        return name
    
    def get_active_automaton_and_state(self) -> tuple[Automaton, State]:
        """The active Automaton paired with its current State — falls back
        to init_action.target if none is persisted yet, or the persisted
        one was renamed/removed on disk since. A pure read, no side
        effect: never returns the reserved implicit state ("") itself, so
        every caller of this (not just ChatService.open_if_needed) always
        sees a real state, whether or not init_action has actually been
        resolved/persisted yet. Raises FileNotFoundError (same exception
        _load_project itself raises for an unknown project name) when
        there's no active project at all — see get_active_project_name's
        own docstring for when that happens."""
        project_name = self.get_active_project_name()
        if project_name is None:
            raise FileNotFoundError("No project is currently active.")
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

    def get_project_signals(self, project_name: str, state_key: str | None = None) -> list[dict]:
        """Signal definitions (name/ui_label/ui_description/attachments) of
        `project_name`'s last successfully saved index.yml — the source for
        the "Edit project" view's Inspect panel. Reads through
        _load_project's cache, which every mutating path (put_project/
        put_project_file/delete_project_file, via _finalize_project_update)
        keeps fresh as of its own last successful save, regardless of which
        project is currently active — so this never reflects an
        in-progress unsaved edit. `relevant` is the authoritative, server-
        computed answer to "is this signal referenced by some action's own
        trigger (or env: field)" — the Inspector Signals tab's own "show
        only relevant signals" filter reads this directly rather than
        re-deriving it client-side. Scoped to `state_key`'s own outgoing
        actions (see Automaton.triggerable_signal_names) when given — the
        Inspector's own currently selected/highlighted state, or (an
        action selected instead) the state it fires *from* — since that's
        the only scope actually meaningful for "would this matter for
        deciding what happens next here." Falls back to every state's
        triggers combined (see Automaton.all_triggerable_signal_names)
        when `state_key` is omitted or no longer a real state (e.g. a
        stale selection from before a rename) — there's always something
        sensible to report, never a hard error over this."""
        automaton = self._load_project(project_name)
        if state_key is not None and state_key in automaton.states:
            relevant_names = automaton.triggerable_signal_names(state_key)
        else:
            relevant_names = automaton.all_triggerable_signal_names()
        return [
            {
                "signal": Automaton.get_signal_payload(signal),
                "relevant": signal.name in relevant_names,
                # Not part of SignalPayload itself (see its own
                # get_signal_payload docstring on why attachments stays
                # empty there) — filenames only, same as a state/action's
                # own attachments (see get_project_graph's node/edge
                # wrappers below), never full content.
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
        else (Tracking.old_state, benchmarkTimeline.js's own synthetic
        session-start entry) for "there was no real prior state"."""
        automaton = self._load_project(project_name)
        real_states = [state for state in automaton.states.values() if state.key != ""]
        nodes = [
            {
                "state": Automaton.get_state_payload(state),
                "is_start": state.key == automaton.init_action.target,
                "history_cutoff": state.history_cutoff,
                "transition_log_level": state.transition_log_level,
                "attachments": list(state.attachments.keys()),
            }
            for state in real_states
        ]
        edges = [
            {
                "action": Automaton.get_action_payload(action),
                "source": state.key,
                # None of these three belong in ActionPayload itself (see
                # its own get_action_payload docstring) — `trigger`
                # especially never reaches a live chat client, only this
                # "Edit project" Inspect-panel-only edge wrapper.
                "trigger": action.trigger,
                "action_prompt": action.action_prompt,
                "ui_description": action.ui_description,
            }
            for state in automaton.states.values()
            for action in state.actions
        ]
        return {"nodes": nodes, "edges": edges}

    def list_projects(self) -> dict:
        names = self._db.list_projects()
        try:
            active = self.get_active_project_name()
        except FileNotFoundError:
            active = None
        return {"projects": names, "active": active}

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
        """Creates or replaces `project_name` from a raw body — a zip
        archive, or (see _looks_like_zip) a single bare YAML file, treated
        as index.yml's own content with no attachments (same one-file
        convention PUT .../files/index.yml already uses for an edit, just
        for the initial upload)."""

        if not self._is_safe_project_name(project_name):
            raise ValueError(f"Invalid project name: '{project_name}'.")

        try:
            if self._looks_like_zip(content_type, content):
                with tempfile.TemporaryDirectory() as tmp:
                    staging_dir = Path(tmp)
                    self._extract_zip_safely(content, staging_dir)
                    files = {
                        file.name: file.read_text()
                        for file in staging_dir.iterdir()
                    }
            else:
                files = {"index.yml": content.decode("utf-8")}
            new_automaton, to_persist = self._prepare_project_update(project_name, files)
        except (zipfile.BadZipFile, ValueError) as exc:
            raise ValueError(str(exc)) from exc
        except Exception as exc:
            logger.exception(exc)
            raise ValueError(f"Invalid project definition: {exc}") from exc

        self._db.set_active_project_name(project_name, Session().user)
        if to_persist is not None:
            self._db.save_project_files(project_name, to_persist)
        await self._finalize_project_update(project_name, new_automaton, commit)

        return {"success": True, "project_name": project_name}

    def _unique_project_name(self, base: str) -> str:
        """`base` itself if free, else the first "`base` N" (N starting at
        2) not already in use — same convention a human would fall back
        to by hand rather than overwrite an existing project of the same
        name."""
        existing = set(self._db.list_projects())
        if base not in existing:
            return base
        suffix = 2
        while f"{base} {suffix}" in existing:
            suffix += 1
        return f"{base} {suffix}"

    async def create_new_project(self, commit: CommitCallback) -> dict:
        """"New project": creates one from NEW_PROJECT_TEMPLATE exactly as
        if the user had picked that same zip in the upload file dialog —
        goes through put_project itself, so validation/staging/commit are
        identical either way. `project_name` is derived from the
        template's own name and de-duplicated (see _unique_project_name)
        since nothing here ever asks the user to type one."""
        content = NEW_PROJECT_TEMPLATE.read_bytes()
        project_name = self._unique_project_name(NEW_PROJECT_NAME)
        return await self.put_project(project_name, content, "application/zip", commit)

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
        """{content, can_undo, can_redo} for `file_name`'s current
        content — can_undo/can_redo are what the "Edit project" view's
        Undo/Redo buttons use to know whether they're enabled, scoped to
        the current user (see db.Db.has_undo/has_redo)."""
        return self._file_undo_redo_info(project_name, file_name)

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
            self._db.save_project_file(Session().user, project_name, file_name, text_content)
        await self._finalize_project_update(project_name, new_automaton, commit)

        return {"success": True, "project_name": project_name, **self._file_undo_redo_info(project_name, file_name)}

    async def _edit_index_yml(self, project_name: str, commit: CommitCallback, operation):
        """Runs `operation(editor: AutomatonYamlEditor) -> T` against
        `project_name`'s own current index.yml text, persists whatever it
        produced through the exact same validation/history/commit path as
        any other file edit (see put_project_file — never a parallel
        write path of its own), and returns `operation`'s own result
        untouched: the newly added/edited/reordered object's own payload,
        never the whole YAML text (see AutomatonYamlEditor's own module
        docstring — every one of its methods already returns exactly
        that shape, or None for a delete)."""
        current = self._file_undo_redo_info(project_name, "index.yml")["content"]
        editor = AutomatonYamlEditor(current)
        result = operation(editor)
        await self.put_project_file(project_name, "index.yml", editor.serialize(), commit)
        return result

    async def add_state(self, project_name: str, commit: CommitCallback) -> StatePayload:
        return await self._edit_index_yml(project_name, commit, lambda editor: editor.add_state())

    async def add_signal(self, project_name: str, commit: CommitCallback) -> SignalPayload:
        return await self._edit_index_yml(project_name, commit, lambda editor: editor.add_signal())

    async def add_action(self, project_name: str, state_name: str, commit: CommitCallback) -> ActionPayload:
        return await self._edit_index_yml(project_name, commit, lambda editor: editor.add_action(state_name))

    async def set_state_field(
        self, project_name: str, state_name: str, field: str, value, commit: CommitCallback
    ) -> StatePayload:
        return await self._edit_index_yml(
            project_name, commit, lambda editor: editor.set_state_field(state_name, field, value)
        )

    async def set_action_field(
        self, project_name: str, state_name: str, action_name: str, field: str, value, commit: CommitCallback
    ) -> ActionPayload:
        return await self._edit_index_yml(
            project_name, commit, lambda editor: editor.set_action_field(state_name, action_name, field, value)
        )

    async def set_signal_field(
        self, project_name: str, signal_name: str, field: str, value, commit: CommitCallback
    ) -> SignalPayload:
        return await self._edit_index_yml(
            project_name, commit, lambda editor: editor.set_signal_field(signal_name, field, value)
        )

    async def delete_state(self, project_name: str, state_name: str, commit: CommitCallback) -> None:
        await self._edit_index_yml(project_name, commit, lambda editor: editor.delete_state(state_name))

    async def delete_action(self, project_name: str, state_name: str, action_name: str, commit: CommitCallback) -> None:
        await self._edit_index_yml(project_name, commit, lambda editor: editor.delete_action(state_name, action_name))

    async def delete_signal(self, project_name: str, signal_name: str, commit: CommitCallback) -> None:
        await self._edit_index_yml(project_name, commit, lambda editor: editor.delete_signal(signal_name))

    async def reorder_actions(
        self, project_name: str, state_name: str, action_name: str, position: int, commit: CommitCallback
    ) -> list[ActionPayload]:
        return await self._edit_index_yml(
            project_name, commit, lambda editor: editor.reorder_actions(state_name, action_name, position)
        )

    async def undo_project_file(self, project_name: str, file_name: str, content: bytes) -> dict:
        """A pure editor preview, not a persisted change (see db.Db.
        undo_project_file) — unlike put_project_file, this never touches
        Archive, never rebuilds/caches the automaton, and never
        reconciles the active conversation: only an explicit Save does
        any of that (see put_project_file). `content` is whatever the
        editor is currently showing (its own live, possibly-unsaved
        state) — needed so a later redo can bring it back. Raises
        ValueError if there's nothing to undo."""
        if project_name not in self._db.list_projects():
            raise FileNotFoundError(f"Project '{project_name}' does not exist.")
        self._check_editable_file_name(file_name)
        text_content = content.decode("utf-8") if isinstance(content, bytes) else content

        user = Session().user
        previous = self._db.undo_project_file(user, project_name, file_name, text_content)
        if previous is None:
            raise ValueError(f"Nothing to undo for file '{file_name}'.")

        return {
            "success": True,
            "project_name": project_name,
            "content": previous,
            "can_undo": self._db.has_undo(user, project_name, file_name),
            "can_redo": self._db.has_redo(user, project_name, file_name),
        }

    async def redo_project_file(self, project_name: str, file_name: str, content: bytes) -> dict:
        """Mirror of undo_project_file, replaying the current user's own
        redo history instead (see db.Db.redo_project_file)."""
        if project_name not in self._db.list_projects():
            raise FileNotFoundError(f"Project '{project_name}' does not exist.")
        self._check_editable_file_name(file_name)
        text_content = content.decode("utf-8") if isinstance(content, bytes) else content

        user = Session().user
        next_content = self._db.redo_project_file(user, project_name, file_name, text_content)
        if next_content is None:
            raise ValueError(f"Nothing to redo for file '{file_name}'.")

        return {
            "success": True,
            "project_name": project_name,
            "content": next_content,
            "can_undo": self._db.has_undo(user, project_name, file_name),
            "can_redo": self._db.has_redo(user, project_name, file_name),
        }

    def clear_project_history(self, project_name: str) -> None:
        """Deletes the current user's own undo/redo history for every
        file in `project_name` (see db.Db.clear_history) — called when
        the "Edit project" view is opened, so a fresh editing session
        never inherits a previous one's undo/redo trail (see
        EditProjectView.vue's own onMounted)."""
        if project_name not in self._db.list_projects():
            raise FileNotFoundError(f"Project '{project_name}' does not exist.")
        self._db.clear_history(Session().user, project_name)

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
        self._db.reset_project(project_name)
        self._db.delete_archives(project_name)
        self._automaton_cache.pop(project_name, None)

        if project_name == self.get_active_project_name():
            # No project name is reserved/preferred for continuity anymore
            # — whatever's left (in whatever order list_projects returns),
            # or nothing at all (leaving the app on its own "select a
            # project" empty state, see App.vue) when the deleted project
            # was the last one.
            remaining = self._db.list_projects()
            fallback = next(iter(remaining), None)
            if fallback is not None:
                await self.activate_project(fallback, commit)
            else:
                self._db.clear_active_project_name(Session().user)
