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
from automaton.automaton_builder import AutomatonBuilder, EXTENSION_TO_MEDIA_TYPE
from automaton.automaton_yaml_editor import AutomatonYamlEditor, InitActionTargetError
from automaton.identifier_registry import build_registry
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
NEW_PROJECT_TEMPLATE = Path(__file__).resolve().parents[2] / "samples" / "projects" / "Hello world.zip"
NEW_PROJECT_NAME = "Hello world"


class ProjectService(object):
    def __init__(self, db: Db) -> None:
        self._db = db
        # (project_name, revision) -> Automaton. Revision-keyed (not just
        # project_name) so a caller pinned to one specific revision (see
        # _load_project_at_revision, get_automaton_and_state_for_session)
        # and a caller that always wants "whatever's current" (see
        # _load_project/get_active_automaton_and_state) can share the same
        # cache without one silently serving the other's revision.
        self._automaton_cache: dict[tuple[str, int], Automaton] = {}

    @staticmethod
    def _is_safe_project_name(project_name: str) -> bool:
        """No path traversal: must be a single plain path segment — not
        empty, not '.'/'..', no separators, resolving to itself when
        treated as a bare filename."""
        if not project_name or project_name in (".", ".."):
            return False
        return Path(project_name).name == project_name

    def _invalidate_automaton_cache(self, project_name: str) -> None:
        """Drops every cached revision of `project_name` at once — a write
        path (revert_to_published/delete_project) that needs this has no
        reason to work out exactly which revision number(s) might now be
        stale, so this just clears all of them. Never needed after an
        ordinary edit (put_project_file/delete_project_file/...): those go
        through _finalize_project_update instead, which already knows
        exactly which one revision it just built and re-caches only that."""
        for key in [k for k in self._automaton_cache if k[0] == project_name]:
            del self._automaton_cache[key]

    def _load_project_at_revision(self, project_name: str, revision: int) -> Automaton:
        cache_key = (project_name, revision)
        cached = self._automaton_cache.get(cache_key)
        if cached is not None:
            return cached

        if not ProjectService._is_safe_project_name(project_name):
            raise ValueError(f"Invalid project name: '{project_name}'.")

        archives = self._db.get_archives(project_name, revision=revision)

        if not archives:
            raise  FileNotFoundError(f"Project '{project_name}' does not exist.")
        if 'index.yml' not in archives:
            raise  FileNotFoundError(f"Project '{project_name}' does not contain 'index.yml'.")

        automaton = AutomatonBuilder().build(archives)
        self._automaton_cache[cache_key] = automaton
        return automaton

    def _load_project(self, project_name: str) -> Automaton:
        """Whatever's current for `project_name` right now — the most
        recent draft, published or not (see Db.get_project_revision). Every
        existing caller of this keeps exactly that behavior; a caller that
        instead needs a specific, possibly older revision (the published
        one specifically, or a particular session's own — see
        get_active_automaton_and_state/get_automaton_and_state_for_session)
        goes through _load_project_at_revision directly instead."""
        revision = self._db.get_project_revision(project_name)
        return self._load_project_at_revision(project_name, revision)

    def _project_update_changed(self, existing: dict[str, str], files: dict[str, str]) -> bool:
        """Whether `files` (new content for some subset of a project's
        own files — see _prepare_project_update) is a genuine change
        against `existing`. A file `existing` doesn't have at all yet is
        always a change, even when its own new content happens to be ""
        (the Explorer's own "+ new file" always creates one this way) —
        `existing.get(name, "")` used to fold that case into "unchanged"
        indistinguishably from a real no-op edit, so the file was never
        actually persisted (see put_project_file's own to_persist branch)
        and the very next read of it 404'd."""
        return any(name not in existing or existing[name] != content for name, content in files.items())

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
        # Same extension-based rule AutomatonBuilder._convert_contents_to_
        # archives uses to build each MemoryArchive's own SourceDict —
        # reused here (rather than re-derived) so the "Edit project" view's
        # file explorer can display it without that meaning something
        # different than what the automaton itself would resolve.
        extension = Path(file_name).suffix.lower()
        media_type = EXTENSION_TO_MEDIA_TYPE.get(extension, "application/octet-stream")
        return {
            "content": content,
            "can_undo": self._db.has_undo(user, project_name, file_name),
            "can_redo": self._db.has_redo(user, project_name, file_name),
            "media_type": media_type,
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

        revision = self._db.get_project_revision(project_name)
        self._automaton_cache[(project_name, revision)] = automaton
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
    
    def _resolve_state(self, project_name: str, automaton: Automaton) -> State:
        """The State half of get_active_automaton_and_state/
        get_automaton_and_state_for_session, factored out since both need
        the exact same resolution against `automaton` — only how
        `automaton` itself gets picked (published-only vs. one session's
        own pinned revision) differs between them. No state persisted yet
        (nothing has ever run) still falls back to init_action.target,
        same as always — that's a legitimate default, not a broken
        reference. A persisted state that no longer exists in `automaton`
        is different: it means a publish once renamed/removed it out from
        under an in-progress conversation, and StateRemap (written at that
        publish, see ProjectService.publish_project — a single lookup no
        matter how many publishes have happened since, since every
        publish that would otherwise orphan a still-open state writes its
        own fresh entry pointing straight at the *current* remap target)
        is the only thing allowed to resolve it — no more silent fallback
        to init_action.target. If StateRemap doesn't have an answer
        either, that's an inconsistency the publish-time check should
        have prevented; raising here is a guardrail, not the expected
        path. A pure read, no side effect: never returns the reserved
        implicit state ("") itself, so every caller of this (not just
        ChatService.open_if_needed) always sees a real state, whether or
        not init_action has actually been resolved/persisted yet."""
        state_key = self._db.get_current_state(project_name)
        if state_key is None:
            state_key = automaton.init_action.target
        elif state_key not in automaton.states:
            remapped = self._db.get_state_remap(project_name, state_key)
            if remapped is None or remapped not in automaton.states:
                raise ValueError(
                    f"Project '{project_name}': persisted state '{state_key}' no longer exists "
                    "and has no StateRemap entry — this should have been caught at publish time."
                )
            state_key = remapped
        return automaton.get_state(state_key)

    def get_active_automaton_and_state(self) -> tuple[Automaton, State]:
        """The active project's *published* Automaton paired with its
        current State — never the in-progress draft, whatever it happens
        to look like right now (see _resolve_state's own docstring for
        the State half). Every caller of this that's about a specific,
        already-existing session instead (see the "6 places" in
        get_automaton_and_state_for_session's own docstring) uses that one
        instead, pinned to that session's own project_revision — this one
        is for every other caller, which never has a session of its own
        to pin to and must never see draft content it didn't explicitly
        ask for (see EditProjectView.vue's own dedicated draft entry
        points, the one place that's still allowed to). Raises
        FileNotFoundError (same exception _load_project itself raises for
        an unknown project name) when there's no active project at all —
        see get_active_project_name's own docstring for when that
        happens — and ValueError, same as db.create_chat_session's own
        "never published" case, when the active project has no published
        revision yet."""
        project_name = self.get_active_project_name()
        if project_name is None:
            raise FileNotFoundError("No project is currently active.")
        published_revision = self._db.get_project_published_revision(project_name)
        if published_revision is None:
            raise ValueError(f"Project '{project_name}' has never been published.")
        automaton = self._load_project_at_revision(project_name, published_revision)
        return automaton, self._resolve_state(project_name, automaton)

    def get_automaton_and_state_for_session(self, session_id: int) -> tuple[Automaton, State]:
        """The Automaton `session_id` was actually stamped against at
        creation time (see ChatSession.project_revision's own docstring —
        native or draft alike, published or not, exactly whatever was
        current the moment this session started) paired with its current
        State (see _resolve_state) — every chat-turn-shaped operation that
        already has a concrete session_id to work from (chat_service.py's
        own truncate_session/open_if_needed/apply_manual_action/
        process_turn, this module's own apply_manual_action, tracking_
        service.py's own process) uses this instead of get_active_
        automaton_and_state, so an in-progress draft edit elsewhere never
        retroactively changes what an already-running session's own turns
        see, and a session pinned to an old, already-superseded revision
        keeps behaving exactly as it did when it was created."""
        session = self._db.get_chat_session(session_id)
        if session is None:
            raise FileNotFoundError(f"Session {session_id} does not exist.")
        project_name = session["project_name"]
        automaton = self._load_project_at_revision(project_name, session["project_revision"])
        return automaton, self._resolve_state(project_name, automaton)

    def get_active_draft_automaton_and_state(self) -> tuple[Automaton, State]:
        """Like get_active_automaton_and_state, but the in-progress draft
        (whatever _load_project resolves to right now) rather than
        published-only — EditProjectView.vue's own dedicated draft session
        entry points (ChatService.create_draft_session/get_or_create_
        current_draft_session) are the only callers: a "Test" session must
        stay creatable against a project that's never been published at
        all yet (see db.create_draft_chat_session), which get_active_
        automaton_and_state's own published-only requirement would
        otherwise block outright before a session (and this call) ever
        even happens."""
        project_name = self.get_active_project_name()
        if project_name is None:
            raise FileNotFoundError("No project is currently active.")
        automaton = self._load_project(project_name)
        return automaton, self._resolve_state(project_name, automaton)

    def apply_manual_action(self, action_name: str, session_id: int) -> tuple[StatePayload, Action, str]:
        """Applies a manual (button) action and returns the destination
        state's payload, the Action that fired, and the source state's
        key (e.g. to detect a self-loop)."""
        automaton, state = self.get_automaton_and_state_for_session(session_id)
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

    def get_active_identifier_registry(self) -> dict[str, dict[str, str]]:
        """Every identifier the active project's own trigger/`env:`
        expressions can reference, one dict per namespace (see automaton.
        identifier_registry.build_registry) — for GET /api/chat/
        identifiers, the "Edit project" view's own reference for what's
        actually usable in a trigger/env: field."""
        automaton, _ = self.get_active_automaton_and_state()
        return build_registry(automaton.signals, automaton.states)

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
                # Not part of StatePayload itself (see its own
                # get_state_payload docstring on why) — a state's own
                # system-prompt text never reaches a live chat client,
                # only this "Edit project" Inspect-panel-only node wrapper
                # (same treatment as action_prompt on the edge wrapper
                # below).
                "contextual_prompt": state.contextual_prompt,
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
        # BenchmarkProjectView.vue's own — an imported session (see
        # ChatSession.source) has no real Tracking rows to resolve which
        # message a mark/annotation point belongs to, so it falls back to
        # whichever side a live turn would actually have evaluated on (see
        # TrackingService._materialize_imported_session_row).
        return {"nodes": nodes, "edges": edges, "autotracking_on_ai_message": automaton.autotracking_on_ai_message}

    def list_projects(self) -> dict:
        names = self._db.list_projects()
        try:
            active = self.get_active_project_name()
        except FileNotFoundError:
            active = None
        return {"projects": names, "active": active}

    def get_project_revision_info(self, project_name: str) -> dict:
        """{revision, published_revision} — the "Edit project" toolbar's
        own revision display, refreshed after every save (a save can fork,
        bumping `revision`) and after every publish."""
        if project_name not in self._db.list_projects():
            raise FileNotFoundError(f"Project '{project_name}' does not exist.")
        return {
            "revision": self._db.get_project_revision(project_name),
            "published_revision": self._db.get_project_published_revision(project_name),
        }

    def preview_publish(self, project_name: str) -> dict:
        """Whether publishing `project_name` right now needs a human state
        remap decision first — the one case where the app itself never
        picks: the current persisted state (mono-user today, see
        Db.get_current_state) has gone missing from the draft that's about
        to become the published revision. No session ever having happened
        (current_state_key is None) is not this case — nothing to remap.
        Also reports `has_active_sessions` — whether any live conversation
        is still actually running on the revision about to be superseded
        (see Db.has_open_sessions_for_revision) — EditProjectView.vue's own
        handlePublish only asks the user to confirm when this is true;
        publishing over a revision nobody's mid-conversation on needs no
        extra prompt."""
        if project_name not in self._db.list_projects():
            raise FileNotFoundError(f"Project '{project_name}' does not exist.")
        draft = self._load_project(project_name)
        current_state_key = self._db.get_current_state(project_name)
        published_revision = self._db.get_project_published_revision(project_name)
        has_active_sessions = (
            published_revision is not None
            and self._db.has_open_sessions_for_revision(project_name, published_revision)
        )
        if current_state_key is None or current_state_key in draft.states:
            return {"needs_remap": False, "has_active_sessions": has_active_sessions}
        return {
            "needs_remap": True,
            "missing_state": current_state_key,
            "available_states": [state.key for state in draft.states.values() if state.key != ""],
            "has_active_sessions": has_active_sessions,
        }

    def publish_project(self, project_name: str, remap_to: str | None = None) -> dict:
        """Sets published_revision = revision for `project_name` — freezing
        the current draft forever (see Db.save_project_files' own fork-on-
        first-edit-after-publish). If the currently persisted state has
        gone missing from that draft (see preview_publish), `remap_to`
        must name a real state in it; the resulting StateRemap entry is
        what get_active_automaton_and_state consults from then on, instead
        of ever guessing (no fallback to init_action.target, no heuristic
        match — the choice is always the caller's, made explicit here)."""
        if project_name not in self._db.list_projects():
            raise FileNotFoundError(f"Project '{project_name}' does not exist.")
        draft = self._load_project(project_name)
        current_state_key = self._db.get_current_state(project_name)
        if current_state_key is not None and current_state_key not in draft.states:
            if remap_to is None:
                raise ValueError(
                    f"State '{current_state_key}' no longer exists in this revision — a remap target is required."
                )
            if remap_to == "" or remap_to not in draft.states:
                raise ValueError(f"'{remap_to}' is not a valid state in this revision.")
            self._db.write_state_remap(project_name, current_state_key, remap_to)
        self._db.publish_project(project_name)
        return self.get_project_revision_info(project_name)

    async def revert_to_published(self, project_name: str, commit: CommitCallback) -> dict:
        """Discards the entire in-progress draft revision, reverting to
        whatever was last published (see Db.revert_to_published) — the
        "Rev. X" split button's own "Revert to rev. X-1" option (see
        EditProjectView.vue), only ever offered there when both a draft-
        ahead-of-published revision and a prior publication exist (a
        stale/duplicate click past that point is a safe no-op, same as
        publish_project)."""
        if project_name not in self._db.list_projects():
            raise FileNotFoundError(f"Project '{project_name}' does not exist.")
        self._db.revert_to_published(project_name)
        self._invalidate_automaton_cache(project_name)
        new_automaton = self._load_project(project_name)
        await self._finalize_project_update(project_name, new_automaton, commit)
        return self.get_project_revision_info(project_name)

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
        self._db.ensure_project(project_name)
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

    async def set_init_action_field(self, project_name: str, field: str, value, commit: CommitCallback):
        return await self._edit_index_yml(
            project_name, commit, lambda editor: editor.set_init_action_field(field, value)
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
        self._invalidate_automaton_cache(project_name)

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
