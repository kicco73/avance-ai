"""Validating/staging/committing project activations, uploads, and
deletions — plus every db.py access tied to "which project/state is
active", encapsulated here so other layers never reach into db.py
themselves for that.
"""
from __future__ import annotations

import hashlib
import io
import json
import logging
import re
import zipfile
import tempfile
from pathlib import Path
from typing import Awaitable, Callable, Mapping

from automaton.automaton import (
    Action, ActionPayload, Automaton, EnvKeyPayload, ProjectPayload, SignalPayload, State, StatePayload,
    trigger_automaton_project_refs,
)
from automaton.automaton_builder import AutomatonBuilder, EXTENSION_TO_MEDIA_TYPE
from automaton.automaton_yaml_editor import AutomatonYamlEditor, InitActionTargetError
from automaton.identifier_registry import build_registry
from events import AvailabilityChanged, publish, subscribe
from session import Session
from db import Db
from tracking.tracking_engine import TrackingEngine
from tracking.session_export import SessionExportManager
from tracking.session_import import SessionImportManager

logger = logging.getLogger(__name__)

# The project zip's own optional "bring your own sessions" file (see
# export_project_zip/put_project below) — never a project file itself
# (excluded from `files` before AutomatonBuilder ever sees it, and never
# persisted as an Archive), just a session_export.py-shaped JSON array,
# imported-only, consumed on upload the same way the "Label sessions"
# view's own JSON upload already is (see SessionImportManager.
# import_session_json) so a project and the reference transcripts it was
# benchmarked against travel together in one file.
SESSIONS_EXPORT_FILENAME = "sessions.json"

# What the file explorer/editor endpoints will read, write, list, or delete —
# index.yml plus the text/plain attachment extensions from
# AutomatonBuilder.EXTENSION_TO_MEDIA_TYPE (binary attachments like .pdf stay
# out of scope for now).
TEXT_EDITABLE_EXTENSIONS = {".yml", ".yaml", ".txt", ".md", ".csv", ".css"}

# Persisted Archive.content_type for each text extension — inferred from the
# extension alone (never from the request), since a text save's own
# Content-Type header is always the generic 'text/plain; charset=utf-8' api.js
# sends regardless of which file it is (see putProjectFile). Unrecognized
# text extensions (only reachable via put_project's zip-upload path, which
# has no per-file extension whitelist of its own) fall back to 'text/plain'.
TEXT_CONTENT_TYPE_BY_EXTENSION = {
    ".yml": "text/yaml",
    ".yaml": "text/yaml",
    ".txt": "text/plain",
    ".md": "text/markdown",
    ".csv": "text/csv",
    ".css": "text/css",
}

# Image attachments an index.css url(...) can reference — the opposite of
# TEXT_EDITABLE_EXTENSIONS: never decoded, always the request's own
# Content-Type (validated against this exact mapping, see put_project_file).
IMAGE_CONTENT_TYPE_BY_EXTENSION = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".svg": "image/svg+xml",
}
IMAGE_EXTENSIONS = set(IMAGE_CONTENT_TYPE_BY_EXTENSION)
IMAGE_CONTENT_TYPES = set(IMAGE_CONTENT_TYPE_BY_EXTENSION.values())

# Everything _check_editable_file_name will let through — text (read,
# decoded, validated as part of the project) or image (opaque bytes, only
# ever referenced from index.css's own url(...), see _missing_css_references).
EDITABLE_EXTENSIONS = TEXT_EDITABLE_EXTENSIONS | IMAGE_EXTENSIONS

# No request-size limit exists anywhere else in this stack (nginx.conf has no
# client_max_body_size, and no application-level limit existed before this).
MAX_IMAGE_UPLOAD_BYTES = 5 * 1024 * 1024


def decode_text_archives(archives: dict[str, bytes]) -> dict[str, str | bytes]:
    """`db.get_archives`/`db.get_archive` are bytes-native (see their own
    docstrings) — this is the one place that turns a raw-bytes archive dict
    back into what AutomatonBuilder.build actually expects: `str` for every
    text file (TEXT_EDITABLE_EXTENSIONS), untouched `bytes` for an image
    (AutomatonBuilder._convert_contents_to_archives already base64-encodes a
    raw-bytes archive correctly on its own, see its own media_type branch —
    only a *text* archive left undecoded would be silently wrapped wrong).
    Shared by every caller that feeds a whole archive dict into
    AutomatonBuilder: _load_project_at_revision, _prepare_project_update,
    and metrics.benchmark_run_service.BenchmarkRunService._load_automaton."""
    decoded: dict[str, str | bytes] = {}
    for name, content in archives.items():
        if Path(name).suffix.lower() in TEXT_EDITABLE_EXTENSIONS and isinstance(content, (bytes, bytearray)):
            decoded[name] = content.decode("utf-8")
        else:
            decoded[name] = content
    return decoded


_CSS_URL_PATTERN = re.compile(r"url\(\s*(['\"]?)([^'\")]+)\1\s*\)", re.IGNORECASE)
_ABSOLUTE_URL_PATTERN = re.compile(r"^(https?:)?//|^data:", re.IGNORECASE)


def missing_css_references(css_text: str, known_archive_names: set[str]) -> list[str]:
    """Every `url(...)` target in `css_text` that isn't one of
    `known_archive_names` — absolute ones (http(s)://, protocol-relative
    //, data:) are never checked, they're not this project's own archives.
    A project's archive namespace is flat (see _is_safe_project_name/
    _check_editable_file_name rejecting any path separator everywhere
    else), so a relative reference like './bg.png' or a bare 'bg.png'
    both resolve the same way: by filename alone."""
    missing = []
    for _, target in _CSS_URL_PATTERN.findall(css_text):
        target = target.strip()
        if not target or _ABSOLUTE_URL_PATTERN.match(target):
            continue
        name = Path(target).name
        if name not in known_archive_names and name not in missing:
            missing.append(name)
    return missing

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
        # Only ever used by export_project_zip/put_project's own
        # sessions.json handling — see SESSIONS_EXPORT_FILENAME's own
        # docstring. Both managers depend on nothing but `db` themselves
        # (see their own modules), so constructing them here carries no
        # circular-import risk the reverse direction (TrackingService
        # already depends on ProjectService) would.
        self._session_export_manager = SessionExportManager(db)
        self._session_import_manager = SessionImportManager(db)
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

    def _known_projects_env_keys(self, project_name: str) -> dict[str, frozenset[str]]:
        """`known_projects` for AutomatonBuilder.build's own Prompt 10
        existence check — every *other* project's own declared project.id
        mapped to its own declared env key names, read via AutomatonBuilder.
        read_declared_env_keys (raw YAML only, at that other project's own
        current revision — never a full build of it, see that method's
        own docstring on why). Skips `project_name` itself (an
        automaton.* reference is only ever meaningful about a *different*
        project) and any other project with no index.yml, or no declared
        project.id at all — nothing to reference it by."""
        known: dict[str, frozenset[str]] = {}
        for other_name in self._db.list_projects():
            if other_name == project_name:
                continue
            archive = self._db.get_archive(other_name, "index.yml")
            if archive is None:
                continue
            project_id, env_keys = AutomatonBuilder.read_declared_env_keys(archive.decode("utf-8"))
            if project_id is not None:
                known[project_id] = env_keys
        return known

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

        automaton = AutomatonBuilder().build(
            decode_text_archives(archives), self._known_projects_env_keys(project_name)
        )
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

    def _project_update_changed(self, existing: Mapping[str, str | bytes], files: Mapping[str, str | bytes]) -> bool:
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

    def _prepare_project_update(
        self, project_name: str, files: Mapping[str, str | bytes]
    ) -> tuple[Automaton, dict[str, str | bytes] | None]:
        """Builds+validates the Automaton for `files` (new content for
        some subset of `project_name`'s own files — all of them for a
        zip upload, just one for a single-file edit) merged onto its
        current ones. `files` is `Mapping`, not `dict` — this never
        writes into it (see the read-only note below), and a plain
        `dict[str, str | bytes]` parameter would otherwise reject a
        caller's own `dict[str, str]` (put_project's own zip/bare-YAML
        branches, never bytes) outright: dict is invariant in its value
        type, Mapping is covariant. Read-only — never writes anything. Returns
        (automaton, to_persist): the second element is the full merged
        file set (put_project hands it straight to db.save_project_files;
        put_project_file only uses its presence as a changed/unchanged
        signal, since it persists just its own single file via
        db.save_project_file), or None when nothing actually changed (see
        _project_update_changed) — the caller's signal to skip
        persistence (and, likely, resetting the active conversation)
        entirely."""
        existing = decode_text_archives(self._db.get_archives(project_name))
        merged = {**existing, **files}

        automaton = AutomatonBuilder().build(merged, self._known_projects_env_keys(project_name))
        self._validate_project_id_globally_unique(project_name, automaton.project_id)

        if not self._project_update_changed(existing, files):
            return automaton, None
        return automaton, merged

    def _validate_project_id_globally_unique(self, project_name: str, project_id: str | None) -> None:
        """The one project.id check AutomatonBuilder itself can't do (see
        its own _build_project_metadata docstring): whether some *other*
        project has already claimed it. Raises before anything gets
        persisted — same "read-only, never writes" contract _prepare_
        project_update's own docstring already promises, so a failed
        save here leaves nothing partially written."""
        if project_id is None:
            return
        owner = self._db.get_project_name_by_project_id(project_id)
        if owner is not None and owner != project_name:
            raise ValueError(
                f"project.id '{project_id}' is already used by project '{owner}' — "
                "project.id must be globally unique."
            )

    def _file_undo_redo_info(self, project_name: str, file_name: str) -> dict:
        content = self._db.get_archive(project_name, file_name)
        if content is None:
            raise FileNotFoundError(f"File '{file_name}' does not exist in project '{project_name}'.")
        content_type = self._db.get_archive_content_type(project_name, file_name)
        user = Session().user
        # Same extension-based rule AutomatonBuilder._convert_contents_to_
        # archives uses to build each MemoryArchive's own SourceDict —
        # reused here (rather than re-derived) so the "Edit project" view's
        # file explorer can display it without that meaning something
        # different than what the automaton itself would resolve.
        extension = Path(file_name).suffix.lower()
        media_type = EXTENSION_TO_MEDIA_TYPE.get(extension, "application/octet-stream")
        # None for an image (or any other binary content_type) — raw bytes
        # aren't JSON-serializable, and nothing reads `content` for a binary
        # file client-side (the file explorer renders it via the raw
        # GET .../content route instead, see get_project_file_content).
        is_text = extension in TEXT_EDITABLE_EXTENSIONS
        return {
            "content": content.decode("utf-8") if is_text else None,
            "can_undo": self._db.has_undo(user, project_name, file_name),
            "can_redo": self._db.has_redo(user, project_name, file_name),
            "content_type": content_type,
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

        # Project metadata (project.id/ui-label/ui-description — see
        # automaton_builder.py's own _build_project_metadata) — synced
        # first: the reverse index below translates *other* projects' own
        # already-synced project_id into their project_name, so this
        # project's own row needs to be current before anything else
        # (itself or another project referencing it) can resolve against
        # it correctly. Global uniqueness was already checked in
        # _prepare_project_update, before any of this ran.
        self._db.set_project_metadata(
            project_name, automaton.project_id, automaton.project_ui_label, automaton.project_ui_description,
        )

        revision = self._db.get_project_revision(project_name)
        self._automaton_cache[(project_name, revision)] = automaton
        # Reverse index (Prompt 6) — every project this one's own
        # self-loop actions reference via automaton.* (see automaton.
        # automaton.trigger_automaton_project_refs), recomputed from
        # scratch on every successful build regardless of whether
        # `project_name` is the active project below (an observer's own
        # reference is meaningful independent of which project the user
        # happens to have open right now). automaton.* names project_id
        # values, never project_name (see _resolve_automaton_project_refs
        # — Prompt 8) — the reverse index itself still stores plain
        # project_name throughout, same as every other project-
        # identifying column in this schema; project_id only ever exists
        # as this one translation.
        observed_project_names = self._resolve_automaton_project_refs(self._automaton_project_refs(automaton))
        self._db.set_project_observers(project_name, observed_project_names)
        # Availability (Prompt 7) — a successful build here already
        # settles this project's own "does it build" half; the other
        # half (every project it depends on is itself available) is
        # exactly what recompute_availability re-checks, off the
        # already-known dependency set set_project_observers just wrote.
        self.recompute_availability(project_name)
        if project_name == self.get_active_project_name():
            current_state_key = self._db.get_current_state(project_name)
            if current_state_key is None or current_state_key not in automaton.states:
                self._db.reset_project(project_name)
            await commit(automaton)
            return True
        return False

    @staticmethod
    def _automaton_project_refs(automaton: Automaton) -> set[str]:
        """Every project_id `automaton`'s own self-loop actions reference
        via automaton.* — raw tokens, not yet resolved to a project_name
        (see _resolve_automaton_project_refs, the one caller that does
        that). Scoped to self-loop actions only (target == the action's
        own containing state) even though automaton_builder.py's own
        build-time check already guarantees no *other* kind of action can
        reference automaton.* at all — the filter here is what actually
        decides the index's own content, not a redundant re-validation of
        something build already enforced."""
        refs: set[str] = set()
        for state in automaton.states.values():
            for action in state.actions:
                if action.trigger and action.target == state.key:
                    refs |= trigger_automaton_project_refs(action.trigger)
        return refs

    def _resolve_automaton_project_refs(self, project_ids: set[str]) -> set[str]:
        """Translates automaton.* reference tokens (project_id values)
        into the project_name each one's own declaring project is
        actually stored under (see db.get_project_name_by_project_id) —
        the reverse index (db.ProjectObserverIndex) stays project_name-
        keyed throughout, same as every other project-identifying column
        in this schema; project_id only ever exists at this one
        translation boundary (Prompt 8). A token matching no known
        project_id yet is silently dropped, not an error — same "a
        dangling reference is a runtime concern, not a build-time
        blocker" reasoning recompute_availability's own docstring already
        documents for a project that doesn't exist at all."""
        names: set[str] = set()
        for project_id in project_ids:
            name = self._db.get_project_name_by_project_id(project_id)
            if name is not None:
                names.add(name)
        return names

    def recompute_availability(self, project_name: str) -> None:
        """Prompt 7 — a project is available exactly when (a) its own
        build succeeds and (b) every project it depends on via
        automaton.* (see _automaton_project_refs/db.get_observed_
        projects) is itself available. (b) is always a cheap read of
        that dependency's own already-computed Project.is_paused flag —
        never a rebuild of its automaton, and never recursive on its
        own: a dependency's own dependencies were already folded into
        *its* own is_paused the last time *it* was recomputed (see the
        AvailabilityChanged cascade below), so this never needs to walk
        the whole chain itself.

        Writes (and publishes AvailabilityChanged) only when the
        recomputed value actually differs from what's already saved —
        the one guard that makes this safe to call from a cascade
        without any cycle detection of its own: a mutual dependency
        between two projects just means each one's own recompute, in
        turn, finds nothing changed on its second visit and stops
        propagating right there (see _on_availability_changed below).

        A manual pause (see Project.manually_paused/set_manually_paused)
        short-circuits every other check below to "not available" — the
        one thing that makes a manual pause survive an unrelated
        recompute (a dependency changing, a rebuild after an unrelated
        edit, ...) without this method needing any special-casing beyond
        this one check: is_paused/paused_reason still go through the
        exact same write-only-on-change path as the automatic case, so
        every existing dependent of this project still sees it as
        unavailable and cascades exactly as it already would for a real
        build/dependency failure. Only set_manually_running (clearing the
        flag) ever lets the real build/dependency state show through
        again."""
        if self._db.get_manually_paused(project_name):
            available, reason = False, "Manually paused."
        else:
            try:
                self._load_project(project_name)
                available, reason = True, None
            except Exception as exc:  # noqa: BLE001 — any failure to build at all means "not available"
                available, reason = False, f"Build failed: {exc}"

            if available:
                blocking = next(
                    (
                        dep for dep in self._db.get_observed_projects(project_name)
                        if (self._db.get_project_availability(dep) or (False, None))[0]
                    ),
                    None,
                )
                if blocking is not None:
                    available, reason = False, f"Depends on unavailable project '{blocking}'."

        current = self._db.get_project_availability(project_name)
        if current is None:
            return  # project no longer exists — nothing left to update
        was_paused, _ = current
        if was_paused == (not available):
            return  # unchanged — see this method's own docstring on why this is the whole guard
        self._db.set_project_availability(project_name, is_paused=not available, paused_reason=reason)
        publish(AvailabilityChanged(project_name=project_name, available=available))

    def register_availability_cascade(self) -> None:
        """Subscribes once, for the whole process's lifetime (see
        main.py's own wiring) — the other half of recompute_availability
        above: whenever *some* project's own availability actually
        changes, every project that depends on it gets a chance to
        change too. Recursive by construction, not by explicit
        recursion: recompute_availability's own guard means a project
        whose recomputed value didn't change never re-publishes, so the
        cascade started here naturally stops propagating outward the
        instant nothing new happens — no queue, no visited-set, no
        explicit BFS of this method's own. It also, for free, wakes
        dependents in the right order when a project comes back: the
        most directly affected one recomputes (and republishes) first,
        which is what lets *its own* dependents react next, and so on
        outward — never the other way around."""
        subscribe(AvailabilityChanged, self._on_availability_changed)

    def _on_availability_changed(self, event: AvailabilityChanged) -> None:
        try:
            for observer in self._db.get_observers(event.project_name):
                self.recompute_availability(observer)
        except Exception:
            logger.exception(
                "Availability cascade failed while reacting to '%s' (available=%s).",
                event.project_name, event.available,
            )

    @staticmethod
    def _project_status(is_paused: bool, manually_paused: bool) -> str:
        """'running' | 'paused' | 'manually_paused' — the three-state
        view of a project's own availability (see Project.is_paused/
        manually_paused's own docstrings). manually_paused always implies
        is_paused (recompute_availability's own short-circuit guarantees
        this — see its own docstring), so checking it first is enough to
        tell the two paused cases apart; 'paused' is only ever the
        automatic one."""
        if manually_paused:
            return "manually_paused"
        if is_paused:
            return "paused"
        return "running"

    def get_runtime_status(self) -> list[dict]:
        """One row per project — name, status ('running'/'paused'/
        'manually_paused'), paused_reason, revision, published_revision —
        for the Settings > Runtime status view (see db.list_projects_
        runtime_status, the raw data this shapes)."""
        return [
            {
                "name": row["name"],
                "status": self._project_status(row["is_paused"], row["manually_paused"]),
                "paused_reason": row["paused_reason"],
                "revision": row["revision"],
                "published_revision": row["published_revision"],
            }
            for row in self._db.list_projects_runtime_status()
        ]

    def set_manually_paused(self, project_name: str) -> dict:
        """Only ever allowed from 'running' (see controller.py's own PUT
        .../pause) — reinforced here, not just left to the UI only
        disabling the button for every other status, since this is the
        one place that actually matters. Persists the flag, then
        immediately recomputes (forcing is_paused True with reason
        "Manually paused." — see recompute_availability's own short-
        circuit) so the existing AvailabilityChanged cascade picks this
        up and propagates to every dependent exactly as it already would
        for a real build/dependency failure, with no separate cascade
        logic of its own."""
        if not self._db.project_exists(project_name):
            raise FileNotFoundError(f"Project '{project_name}' does not exist.")
        is_paused, _ = self._db.get_project_availability(project_name) or (False, None)
        manually_paused = self._db.get_manually_paused(project_name) or False
        status = self._project_status(is_paused, manually_paused)
        if status != "running":
            raise ValueError(f"Project '{project_name}' isn't running (status: '{status}') — can't be manually paused.")
        self._db.set_manually_paused(project_name, True)
        self.recompute_availability(project_name)
        return self.get_project_runtime_status(project_name)

    def set_manually_running(self, project_name: str) -> dict:
        """The other half of set_manually_paused — only ever allowed from
        'manually_paused', clearing the flag and letting
        recompute_availability report the real, current build/dependency
        state again (which may or may not actually be 'running' — a
        dependency could have gone down in the meantime)."""
        if not self._db.project_exists(project_name):
            raise FileNotFoundError(f"Project '{project_name}' does not exist.")
        is_paused, _ = self._db.get_project_availability(project_name) or (False, None)
        manually_paused = self._db.get_manually_paused(project_name) or False
        status = self._project_status(is_paused, manually_paused)
        if status != "manually_paused":
            raise ValueError(f"Project '{project_name}' isn't manually paused (status: '{status}') — can't be resumed.")
        self._db.set_manually_paused(project_name, False)
        self.recompute_availability(project_name)
        return self.get_project_runtime_status(project_name)

    def get_project_runtime_status(self, project_name: str) -> dict:
        """One row, same shape as get_runtime_status's own — the
        pause/resume endpoints' own return value, so the caller can
        refresh just that row without re-fetching the whole table."""
        is_paused, paused_reason = self._db.get_project_availability(project_name) or (False, None)
        manually_paused = self._db.get_manually_paused(project_name) or False
        return {
            "name": project_name,
            "status": self._project_status(is_paused, manually_paused),
            "paused_reason": paused_reason,
            "revision": self._db.get_project_revision(project_name),
            "published_revision": self._db.get_project_published_revision(project_name),
        }

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

    def get_project_availability(self, project_name: str) -> tuple[bool, str | None]:
        """(is_paused, paused_reason) — see recompute_availability's own
        docstring for how these are decided. (False, None) for a project
        that doesn't exist at all (never raises): every caller of this
        already has its own, more specific way to report "no such
        project" if that distinction actually matters to it."""
        return self._db.get_project_availability(project_name) or (False, None)

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

    def get_automaton_and_state(self, project_name: str) -> tuple[Automaton, State]:
        """`project_name`'s own *published* Automaton paired with its
        current State — never the in-progress draft (see _resolve_state's
        own docstring for the State half). Explicit-project_name sibling
        of get_active_automaton_and_state below (which is now just a thin
        wrapper over this, pinned to get_active_project_name()) — for
        callers that already know exactly which project they mean (e.g.
        LabelProjectView.vue's own Metrics tab, GET /api/chat/identifiers)
        and must never silently drift onto whatever else happens to be
        globally active instead. Raises ValueError, same as db.
        create_chat_session's own "never published" case, when
        `project_name` has no published revision yet."""
        published_revision = self._db.get_project_published_revision(project_name)
        if published_revision is None:
            raise ValueError(f"Project '{project_name}' has never been published.")
        automaton = self._load_project_at_revision(project_name, published_revision)
        return automaton, self._resolve_state(project_name, automaton)

    def get_active_automaton_and_state(self) -> tuple[Automaton, State]:
        """The active project's own get_automaton_and_state (see that
        method's own docstring) — never the in-progress draft, whatever
        it happens to look like right now. Every caller of this that's
        about a specific, already-existing session instead (see the "6
        places" in get_automaton_and_state_for_session's own docstring)
        uses that one instead, pinned to that session's own
        project_revision — this one is for every other caller, which
        never has a session of its own to pin to and must never see draft
        content it didn't explicitly ask for (see EditProjectView.vue's
        own dedicated draft entry points, the one place that's still
        allowed to). Raises FileNotFoundError (same exception
        _load_project itself raises for an unknown project name) when
        there's no active project at all — see get_active_project_name's
        own docstring for when that happens."""
        project_name = self.get_active_project_name()
        if project_name is None:
            raise FileNotFoundError("No project is currently active.")
        return self.get_automaton_and_state(project_name)

    def get_automaton_and_state_for_session(self, session_id: int) -> tuple[Automaton, State]:
        """The Automaton `session_id`'s own turns must run against, paired
        with its current State (see _resolve_state) — every chat-turn-
        shaped operation that already has a concrete session_id to work
        from (chat_service.py's own truncate_session/open_if_needed/
        apply_manual_action/process_turn, this module's own apply_manual_
        action, tracking_service.py's own process) uses this instead of
        get_active_automaton_and_state.

        A native session is pinned to the Automaton it was actually
        stamped against at creation time (see ChatSession.project_revision's
        own docstring — whatever was published the moment this session
        started), so an in-progress draft edit elsewhere never
        retroactively changes what an already-running session's own turns
        see, and a session pinned to an old, already-superseded revision
        keeps behaving exactly as it did when it was created.

        A 'test' session (see db.create_draft_chat_session) is the one
        exception: EditProjectView.vue's own embedded "Test" chat exists
        precisely to test whatever's being edited *right now*, not
        whatever the draft happened to look like when the session was
        first bootstrapped — every turn re-resolves against the live
        draft (same _load_project call as get_active_draft_automaton_and_
        state), same as create_draft_session/get_or_create_current_draft_
        session already do for a brand new session."""
        session = self._db.get_chat_session(session_id)
        if session is None:
            raise FileNotFoundError(f"Session {session_id} does not exist.")
        project_name = session["project_name"]
        if session["source"] == "test":
            automaton = self._load_project(project_name)
        else:
            automaton = self._load_project_at_revision(project_name, session["project_revision"])
        return automaton, self._resolve_state(project_name, automaton)

    def get_automaton_and_state_for_observer(
        self, project_name: str, username: str
    ) -> tuple[Automaton, State] | None:
        """`project_name`'s own published Automaton, paired with its
        current State, as seen by `username` right now — for
        tracking.automaton_namespace's own automaton.<project>.state/
        env.<key> resolution (a self-loop-only trigger in some *other*
        project, referencing this one). None (never raised) when
        `username` has no session in `project_name` at all — that's a
        legitimate, routine outcome here (see automaton_namespace's own
        'no_session' SystemWarning), not an error condition the way it
        would be for get_automaton_and_state_for_session (which always
        already has a concrete, real session_id to work from). Still
        raises FileNotFoundError, same as _load_project_at_revision
        itself, when `project_name` doesn't exist at all — the caller's
        own 'project_not_found' case. Checked explicitly, before the
        session lookup below (rather than just letting
        _load_project_at_revision raise it naturally once reached): a
        project that doesn't exist at all also has no ChatSession rows
        for anyone, so the session check alone would otherwise report
        'no_session' for it too, indistinguishable from a real,
        never-talked-to *existing* project — exactly the two distinct
        SystemWarning kinds this method exists to tell apart."""
        if not self._db.project_exists(project_name):
            raise FileNotFoundError(f"Project '{project_name}' does not exist.")
        session = self._db.get_latest_chat_session(username, project_name)
        if session is None:
            return None
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
        # Events (Prompt 6) — the other of TrackingEngine.
        # notify_transition's own two call sites (see that method's own
        # docstring): this path never goes through TrackingEngine.
        # apply_transition itself (it writes save_transition directly,
        # above), so it has to publish explicitly. The session's own
        # stored username (not Session().user) is the correct "whose
        # transition this is" — see get_chat_session's own row shape.
        session = self._db.get_chat_session(session_id)
        assert session is not None  # get_automaton_and_state_for_session above already resolved this same session_id
        TrackingEngine.notify_transition(session["username"], session["project_name"], state.key, new_state.key)
        return automaton.get_state_payload(new_state), action, state.key

    def get_active_state_payload(self) -> StatePayload:
        automaton, state = self.get_active_automaton_and_state()
        return automaton.get_state_payload(state)

    def reset_active_project(self) -> None:
        # User-scoped (see db.reset_project_for_user): only the current
        # user's own sessions/messages/signals for this project are wiped
        # — not every user's, unlike delete_project's full reset_project.
        self._db.reset_project_for_user(Session().user, self.get_active_project_name())

    def _resolve_inspector_revision(self, project_name: str, session_id: int | None) -> int:
        """The revision an Inspect-panel read (get_project_graph/
        get_project_signals/get_project_env_keys/get_project_file_content)
        should read `project_name` at. `session_id` omitted resolves
        against the current draft — every "Edit project" caller's own
        case, same as _load_project already does. Given, resolves the
        exact same revision get_automaton_and_state_for_session would
        build that session's own automaton against: whatever was stamped
        on it at creation for a real session, or (a 'test' session, which
        always tracks the live draft — see that method's own docstring on
        why) the current draft too, never just session['project_revision']
        uniformly. LabelProjectView.vue's own case — reviewing an older
        session must never show today's structure once it's since
        diverged (a renamed/deleted/added state or signal)."""
        if session_id is None:
            return self._db.get_project_revision(project_name)
        session = self._db.get_chat_session(session_id)
        if session is None:
            raise FileNotFoundError(f"Session {session_id} does not exist.")
        if session["source"] == "test":
            return self._db.get_project_revision(project_name)
        return session["project_revision"]

    def get_project_signals(
        self, project_name: str, state_key: str | None = None, session_id: int | None = None
    ) -> list[dict]:
        """Signal definitions (name/ui_label/ui_description/attachments) of
        `project_name`'s index.yml, for the "Edit project"/"Label sessions"
        views' own Inspect panel — see _resolve_inspector_revision for
        which revision that actually means. Reads through _load_project_at_
        revision's own cache, which every mutating path (put_project/
        put_project_file/delete_project_file, via _finalize_project_update)
        keeps fresh as of its own last successful save for the current
        draft specifically — an older, already-superseded revision's own
        cache entry is immutable once built (that revision's own archives
        can never change again), so never needs invalidating at all.
        `relevant` is the authoritative, server-computed answer to "is this
        signal referenced by some action's own trigger (or env: field)" —
        the Inspector Signals tab's own "show only relevant signals" filter
        reads this directly rather than re-deriving it client-side. Scoped
        to `state_key`'s own outgoing actions (see Automaton.
        triggerable_signal_names) when given — the Inspector's own
        currently selected/highlighted state, or (an action selected
        instead) the state it fires *from* — since that's the only scope
        actually meaningful for "would this matter for deciding what
        happens next here." Falls back to every state's triggers combined
        (see Automaton.all_triggerable_signal_names) when `state_key` is
        omitted or no longer a real state (e.g. a stale selection from
        before a rename) — there's always something sensible to report,
        never a hard error over this."""
        automaton = self._load_project_at_revision(
            project_name, self._resolve_inspector_revision(project_name, session_id)
        )
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

    def get_project_env_keys(self, project_name: str, session_id: int | None = None) -> list[dict]:
        """Env-key declarations (name/ui_description/value) of
        `project_name`'s index.yml — the source for the "Edit project"
        view's own Inspect panel Env tab, same revision/cache/staleness
        contract as get_project_signals above."""
        automaton = self._load_project_at_revision(
            project_name, self._resolve_inspector_revision(project_name, session_id)
        )
        return [{"env_key": Automaton.get_env_key_payload(env_key)} for env_key in automaton.env_keys]

    def get_project_metadata(self, project_name: str) -> ProjectPayload:
        """The optional top-level `project:` section (id/ui_label/
        ui_description) of `project_name`'s last successfully saved
        index.yml — the source for the "Edit project" view's own Inspect
        panel Info tab, same cache/staleness contract as
        get_project_signals/get_project_env_keys above. Read straight off
        the already-built Automaton (see AutomatonBuilder._build_project_
        metadata) rather than re-parsing the YAML text — this is exactly
        the same project_id/project_ui_label/project_ui_description the
        automaton.* namespace itself resolves against."""
        automaton = self._load_project(project_name)
        return {
            "id": automaton.project_id,
            "ui_label": automaton.project_ui_label,
            "ui_description": automaton.project_ui_description,
        }

    def get_active_identifier_registry(self, project_name: str | None = None) -> dict[str, dict[str, str]]:
        """Every identifier `project_name`'s own trigger/`env:`
        expressions can reference, one dict per namespace (see automaton.
        identifier_registry.build_registry) — for GET /api/chat/
        identifiers, the "Edit project" view's own reference for what's
        actually usable in a trigger/env: field. `project_name` omitted
        falls back to the active project (same as before this parameter
        existed) — EditProjectView.vue's own caller always passes its own
        props.projectName explicitly instead, so a trigger editor open on
        project A never silently reflects whatever project B happens to
        be globally active right now.

        build_registry itself stays single-project (see its own
        docstring) — the "automaton" namespace is cross-project by
        nature (see automaton.trigger_automaton_project_refs, Prompt 6),
        so it's folded in here instead, straight off every *other*
        project's own declared project.id (Prompt 8/9 — automaton.*
        resolves against project_id, never the raw project_name, and a
        project with no declared project_id is never exposed to another
        project's own automaton.* at all): "automaton" itself (always
        present, even empty, so it's offered as a top-level namespace —
        see triggerEditorSupport.js's own completeIdentifiers, which
        derives "automaton.<id>"/"automaton.<id>.env" as further sub-
        namespaces to descend into purely from which registry keys
        exist, the same generic mechanism session.metric already relied
        on), then one "automaton.<id>" entry per other project that
        declares one (just `state`) and one "automaton.<id>.env" entry
        (that project's own declared env keys, see automaton_yaml_
        editor.py's env: section) — never the active project itself,
        since referencing your own current state through automaton.*
        rather than plain state.key would be pointless indirection. A
        project that currently fails to build still gets its own "state"
        entry (offered, not guaranteed usable — same as any automaton.*
        reference already resolves gracefully to None at runtime, see
        AutomatonNamespace), just no env keys to show for it."""
        resolved_project_name = project_name or self.get_active_project_name()
        automaton, _ = self.get_automaton_and_state(resolved_project_name)
        registry = build_registry(automaton.signals, automaton.env_keys)
        registry["automaton"] = {}
        for name in self._db.list_projects():
            if name == resolved_project_name:
                continue
            project_id = self._db.get_project_id(name)
            if project_id is None:
                continue
            registry[f"automaton.{project_id}"] = {"state": f"The '{name}' project's own current state."}
            try:
                other_automaton = self._load_project(name)
            except Exception:  # noqa: BLE001 — still offerable via .state, just without its own env keys
                env_keys = {}
            else:
                env_keys = {env_key.name: env_key.ui_description or "" for env_key in other_automaton.env_keys}
            registry[f"automaton.{project_id}.env"] = env_keys
        return registry

    def get_project_states(self, project_name: str) -> list[str]:
        """Every real state key of `project_name`'s current draft
        automaton, excluding the reserved "" pseudo-state (see
        AutomatonBuilder.build) — same exclusion as get_project_graph's
        own `real_states`, just the keys alone, for a caller (the "Stati"
        branch's own node list — see TestsTree.vue) that has no use for
        the rest of the graph payload."""
        automaton = self._load_project(project_name)
        return [state.key for state in automaton.states.values() if state.key != ""]

    def get_project_graph(self, project_name: str, session_id: int | None = None) -> dict:
        """The project's state machine as nodes (states) and edges
        (actions) — the source for the "Edit project"/"Label sessions"
        views' own Inspect panel graph (rendered client-side with
        Cytoscape). Reads through the same _load_project_at_revision
        cache as get_project_signals — see _resolve_inspector_revision for
        which revision that actually means; a LabelProjectView.vue review
        session pins this to the exact revision it ran against, so an
        older session never shows a state/action that's since been
        renamed or deleted (or is missing one added since). The reserved
        implicit state ("", see AutomatonBuilder.build) is never a real
        state and is excluded from `nodes` — each real node's own
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
        revision = self._resolve_inspector_revision(project_name, session_id)
        automaton = self._load_project_at_revision(project_name, revision)
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
        return {
            "nodes": nodes, "edges": edges, "autotracking_on_ai_message": automaton.autotracking_on_ai_message,
            # The exact revision this graph was actually built from (see
            # _resolve_inspector_revision) — InspectorGraph.vue's own
            # "Rev. X" badge reads this straight off the same response
            # rather than a second, possibly out-of-sync fetch, so it's
            # always right regardless of whether session_id pinned this to
            # something other than the current draft.
            "revision": revision,
        }

    def list_projects(self) -> dict:
        projects = self._db.list_projects_with_availability()
        try:
            active = self.get_active_project_name()
        except FileNotFoundError:
            active = None
        return {"projects": projects, "active": active}

    def get_project_revision_info(self, project_name: str) -> dict:
        """{revision, published_revision, is_paused, paused_reason} — the
        "Edit project" toolbar's own revision display, refreshed after
        every save (a save can fork, bumping `revision`) and after every
        publish. is_paused/paused_reason (Prompt 7) ride along on this
        same, already-refreshed-on-every-relevant-event payload rather
        than a second endpoint of their own — EditProjectView.vue's own
        "this project is paused" warning banner reads them straight off
        it."""
        if project_name not in self._db.list_projects():
            raise FileNotFoundError(f"Project '{project_name}' does not exist.")
        is_paused, paused_reason = self._db.get_project_availability(project_name) or (False, None)
        return {
            "revision": self._db.get_project_revision(project_name),
            "published_revision": self._db.get_project_published_revision(project_name),
            "is_paused": is_paused,
            "paused_reason": paused_reason,
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
            # SESSIONS_EXPORT_FILENAME (see its own docstring) is never a
            # project file — pulled out before AutomatonBuilder ever sees
            # `files`, parsed eagerly (a malformed one fails the whole
            # upload here, same as a malformed index.yml would, rather
            # than partially succeeding), imported for real only once the
            # project itself is fully committed below.
            raw_sessions = files.pop(SESSIONS_EXPORT_FILENAME, None)
            sessions_to_import = self._parse_sessions_export(raw_sessions)
            new_automaton, to_persist = self._prepare_project_update(project_name, files)
        except (zipfile.BadZipFile, ValueError) as exc:
            raise ValueError(str(exc)) from exc
        except Exception as exc:
            logger.exception(exc)
            raise ValueError(f"Invalid project definition: {exc}") from exc

        self._db.set_active_project_name(project_name, Session().user)
        self._db.ensure_project(project_name)
        if to_persist is not None:
            # This upload path is text-only (zip entries are always read via
            # read_text() above, never as bytes) — content_type is inferred
            # from each entry's own extension, same as put_project_file.
            to_persist_bytes = {
                name: value.encode("utf-8") if isinstance(value, str) else value
                for name, value in to_persist.items()
            }
            content_types = {
                name: TEXT_CONTENT_TYPE_BY_EXTENSION.get(Path(name).suffix.lower(), "text/plain")
                for name in to_persist
            }
            self._db.save_project_files(project_name, to_persist_bytes, content_types)
        await self._finalize_project_update(project_name, new_automaton, commit)
        self._import_sessions_export(project_name, sessions_to_import)

        return {"success": True, "project_name": project_name}

    @staticmethod
    def _parse_sessions_export(raw_sessions: str | None) -> list[dict]:
        """None (SESSIONS_EXPORT_FILENAME wasn't in the upload at all) ->
        []. Otherwise must be a JSON array of session objects (session_
        export.py's own shape) — anything else raises ValueError, caught
        by put_project's own broad except right alongside every other
        upload-validation failure."""
        if raw_sessions is None:
            return []
        try:
            parsed = json.loads(raw_sessions)
        except json.JSONDecodeError as exc:
            raise ValueError(f"'{SESSIONS_EXPORT_FILENAME}' is not valid JSON: {exc}") from exc
        if not isinstance(parsed, list):
            raise ValueError(f"'{SESSIONS_EXPORT_FILENAME}' must be a JSON array of sessions.")
        return parsed

    def _import_sessions_export(self, project_name: str, sessions: list[dict]) -> None:
        """Best-effort, one session at a time: a single malformed entry
        (see SessionImportManager.import_session_json's own docstring on
        what that looks like) is skipped, logged, and never blocks the
        rest — the project itself is already fully committed by the time
        this runs, so there's nothing left to roll back to, and rejecting
        every *other*, perfectly good session over one bad one would only
        make this feature less trustworthy to rely on.

        A no-op for an empty list (no sessions.json in the upload at all
        — the overwhelmingly common case) — but when there *is* at least
        one session to import, this publishes `project_name` first: every
        ChatSession, imported or not, is always stamped against a real
        published revision (see db.create_chat_session), so without this
        a brand-new upload's own sessions.json would fail outright until
        someone separately hit Publish. Requiring that extra manual step
        just to make sessions.json "just work" on upload would defeat
        the point of it being automatic at all."""
        if not sessions:
            return
        self._db.publish_project(project_name)
        username = Session().user
        for session_data in sessions:
            try:
                self._session_import_manager.import_session_json(username, project_name, session_data)
            except (ValueError, KeyError, TypeError):
                logger.exception(
                    "Skipped a malformed session while importing '%s' from '%s'.",
                    project_name, SESSIONS_EXPORT_FILENAME,
                )

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
        """`project_name`'s own files, round-trippable back through
        put_project unchanged — plus, when there's at least one, a
        SESSIONS_EXPORT_FILENAME holding every *imported* session (see
        that constant's own docstring on why imported-only: a native
        session only ever means something against the exact database it
        was actually played against). Never added when there are none —
        keeps a zip with nothing to carry along exactly as it always
        looked before this existed."""
        archives = self._db.get_archives(project_name)
        if archives is None:
            raise FileNotFoundError(f"Project '{project_name}' does not exist.")

        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
            for archive_name, archive_content in archives.items():
                zf.writestr(archive_name, archive_content)
            imported_sessions = self._session_export_manager.export_sessions(
                Session().user, project_name, source='imported',
            )
            if imported_sessions:
                zf.writestr(SESSIONS_EXPORT_FILENAME, json.dumps(imported_sessions, indent=2))

        return buffer.getvalue()

    @staticmethod
    def _check_editable_file_name(file_name: str) -> None:
        """Everything the file explorer/editor endpoints (put_project_file/
        undo_project_file/redo_project_file — the only three callers) will
        write: a flat, non-hidden file name (no path traversal) with one of
        EDITABLE_EXTENSIONS — index.yml, its text attachments, or an image
        attachment. Anything else in a project's directory stays out of
        scope."""
        if not file_name or file_name in (".", "..") or Path(file_name).name != file_name:
            raise ValueError(f"Invalid file name: '{file_name}'.")
        if file_name.startswith("."):
            raise ValueError(f"Invalid file name: '{file_name}'.")
        extension = Path(file_name).suffix.lower()
        if extension not in EDITABLE_EXTENSIONS:
            raise ValueError(
                f"Unsupported file '{file_name}': only {sorted(EDITABLE_EXTENSIONS)} "
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

    def get_project_file_content(
        self, project_name: str, file_name: str, session_id: int | None
    ) -> tuple[bytes, str]:
        """Raw (content, content_type) for `file_name` — the ChatWindow.vue
        skin-loading fetch (index.css, injected as a stylesheet) and the
        EditProjectView.vue file explorer's own image preview both read
        through this, never the JSON get_project_file above (bytes aren't
        JSON-serializable, and neither caller wants the JSON envelope).

        `session_id` absent: the current draft (same default GET
        .../files/{file_name} already uses) — the editor's own case.

        `session_id` given: resolves the *exact same* revision
        _resolve_inspector_revision (shared with get_project_graph/
        get_project_signals/get_project_env_keys) would — deliberately
        mirrored, not just "use session['project_revision']" uniformly: a
        'test' session there always re-resolves against whatever the
        draft currently is (see get_automaton_and_state_for_session's own
        docstring on why — it's meant to reflect in-progress edits, not a
        frozen snapshot from whenever the session was created), while
        every other session is pinned to the exact revision stamped on it
        at creation time. Getting this wrong would only misbehave in a
        rare edge case (a publish + new edit elsewhere while a Test
        session is still open), but it's the one place this route could
        silently serve a stale skin, so it's worth doing right rather
        than literally."""
        revision = self._resolve_inspector_revision(project_name, session_id)
        content = self._db.get_archive(project_name, file_name, revision=revision)
        if content is None:
            raise FileNotFoundError(f"File '{file_name}' does not exist in project '{project_name}'.")
        content_type = self._db.get_archive_content_type(project_name, file_name, revision=revision)
        assert content_type is not None  # same Archive row get_archive above already found content for
        return content, content_type

    async def put_project_file(
        self, project_name: str, file_name: str, content: bytes | str, content_type_header: str | None,
        commit: CommitCallback,
    ) -> dict:
        """Creates or edits one of `project_name`'s files in place. A text
        extension (TEXT_EDITABLE_EXTENSIONS) is always decoded as UTF-8 and
        its content_type inferred from the extension alone, regardless of
        `content_type_header` (api.js's own putProjectFile always sends the
        generic 'text/plain; charset=utf-8' for every text file — the
        extension is the only thing that actually distinguishes them). An
        image extension (IMAGE_EXTENSIONS) instead requires
        `content_type_header` to be one of IMAGE_CONTENT_TYPES *and* match
        the extension, stays raw bytes end to end, and is capped at
        MAX_IMAGE_UPLOAD_BYTES — no other size limit exists anywhere in this
        stack (see nginx.conf). `index.css` specifically also runs CSS
        url(...) reference validation before anything is persisted (see
        missing_css_references) — every other extension skips that step.
        `content` accepts `str` directly (not just `bytes`) for a text
        extension specifically — _edit_index_yml's own callers already
        have AutomatonYamlEditor.serialize()'s own `str` in hand and would
        otherwise have to encode it just to be decoded right back a few
        lines down (see the isinstance check immediately below)."""
        if project_name not in self._db.list_projects():
            raise FileNotFoundError(f"Project '{project_name}' does not exist.")

        self._check_editable_file_name(file_name)
        extension = Path(file_name).suffix.lower()

        if extension in TEXT_EDITABLE_EXTENSIONS:
            text_content = content.decode("utf-8") if isinstance(content, bytes) else content
            content_type = TEXT_CONTENT_TYPE_BY_EXTENSION.get(extension, "text/plain")
            if file_name == "index.css":
                known_names = set(self._db.list_archives(project_name)) | {file_name}
                missing = missing_css_references(text_content, known_names)
                if missing:
                    raise ValueError(
                        f"index.css references missing file(s): {', '.join(sorted(missing))}."
                    )
            update_value: str | bytes = text_content
            to_save: bytes = text_content.encode("utf-8")
        else:
            # Only a text extension (see the `str` branch above) ever
            # legitimately hands this a `str` — an image upload is always
            # real bytes off the request body, never AutomatonYamlEditor.
            # serialize()'s own text.
            assert isinstance(content, bytes)
            expected_content_type = IMAGE_CONTENT_TYPE_BY_EXTENSION[extension]
            if content_type_header != expected_content_type:
                raise ValueError(
                    f"Unsupported or mismatched Content-Type for '{file_name}': expected "
                    f"'{expected_content_type}', got '{content_type_header}'."
                )
            if len(content) > MAX_IMAGE_UPLOAD_BYTES:
                raise ValueError(f"'{file_name}' exceeds the {MAX_IMAGE_UPLOAD_BYTES}-byte upload limit.")
            content_type = expected_content_type
            update_value = content
            to_save = content

        try:
            new_automaton, to_persist = self._prepare_project_update(project_name, {file_name: update_value})
        except Exception as exc:
            raise ValueError(f"Invalid project update: {exc}") from exc

        if to_persist is not None:
            self._db.save_project_file(Session().user, project_name, file_name, to_save, content_type)
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
        await self.put_project_file(project_name, "index.yml", editor.serialize(), None, commit)
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

    async def set_project_field(self, project_name: str, field: str, value, commit: CommitCallback) -> ProjectPayload:
        return await self._edit_index_yml(
            project_name, commit, lambda editor: editor.set_project_field(field, value)
        )

    async def delete_state(self, project_name: str, state_name: str, commit: CommitCallback) -> None:
        await self._edit_index_yml(project_name, commit, lambda editor: editor.delete_state(state_name))

    async def delete_action(self, project_name: str, state_name: str, action_name: str, commit: CommitCallback) -> None:
        await self._edit_index_yml(project_name, commit, lambda editor: editor.delete_action(state_name, action_name))

    async def delete_signal(self, project_name: str, signal_name: str, commit: CommitCallback) -> None:
        await self._edit_index_yml(project_name, commit, lambda editor: editor.delete_signal(signal_name))

    async def add_env_key(self, project_name: str, commit: CommitCallback) -> EnvKeyPayload:
        return await self._edit_index_yml(project_name, commit, lambda editor: editor.add_env_key())

    async def set_env_key_field(
        self, project_name: str, env_key_name: str, field: str, value, commit: CommitCallback
    ) -> EnvKeyPayload:
        return await self._edit_index_yml(
            project_name, commit, lambda editor: editor.set_env_key_field(env_key_name, field, value)
        )

    async def delete_env_key(self, project_name: str, env_key_name: str, commit: CommitCallback) -> None:
        await self._edit_index_yml(project_name, commit, lambda editor: editor.delete_env_key(env_key_name))

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
        is_text = Path(file_name).suffix.lower() in TEXT_EDITABLE_EXTENSIONS
        raw_content = content.encode("utf-8") if is_text and isinstance(content, str) else content

        user = Session().user
        previous = self._db.undo_project_file(user, project_name, file_name, raw_content)
        if previous is None:
            raise ValueError(f"Nothing to undo for file '{file_name}'.")

        return {
            "success": True,
            "project_name": project_name,
            "content": previous.decode("utf-8") if is_text else None,
            "can_undo": self._db.has_undo(user, project_name, file_name),
            "can_redo": self._db.has_redo(user, project_name, file_name),
        }

    async def redo_project_file(self, project_name: str, file_name: str, content: bytes) -> dict:
        """Mirror of undo_project_file, replaying the current user's own
        redo history instead (see db.Db.redo_project_file)."""
        if project_name not in self._db.list_projects():
            raise FileNotFoundError(f"Project '{project_name}' does not exist.")
        self._check_editable_file_name(file_name)
        is_text = Path(file_name).suffix.lower() in TEXT_EDITABLE_EXTENSIONS
        raw_content = content.encode("utf-8") if is_text and isinstance(content, str) else content

        user = Session().user
        next_content = self._db.redo_project_file(user, project_name, file_name, raw_content)
        if next_content is None:
            raise ValueError(f"Nothing to redo for file '{file_name}'.")

        return {
            "success": True,
            "project_name": project_name,
            "content": next_content.decode("utf-8") if is_text else None,
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
            new_automaton = AutomatonBuilder().build(archives, self._known_projects_env_keys(project_name))
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
