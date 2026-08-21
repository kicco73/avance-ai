"""Validates, stages, and commits project activations, uploads, and
deletions. Also owns every db.py access tied to "which project/state is
active", so other layers never reach into db.py directly for that."""
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

import tinycss2

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

# Optional "bring your own sessions" file in a project zip — a
# session_export.py-shaped JSON array, imported-only, never a project file
# (excluded before AutomatonBuilder sees `files`, never persisted as Archive).
SESSIONS_EXPORT_FILENAME = "sessions.json"

# What the file explorer/editor endpoints read, write, list, or delete —
# index.yml plus the text/plain attachment extensions.
TEXT_EDITABLE_EXTENSIONS = {".yml", ".yaml", ".txt", ".md", ".csv", ".css"}

# Persisted Archive.content_type per text extension, inferred from the
# extension alone: the request's Content-Type header is always the generic
# 'text/plain; charset=utf-8' regardless of which file it is.
TEXT_CONTENT_TYPE_BY_EXTENSION = {
    ".yml": "text/yaml",
    ".yaml": "text/yaml",
    ".txt": "text/plain",
    ".md": "text/markdown",
    ".csv": "text/csv",
    ".css": "text/css",
}

# Image attachments an index.css url(...) can reference — opaque bytes,
# never decoded, Content-Type validated against this exact mapping.
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

# Everything _check_editable_file_name lets through: text or image.
EDITABLE_EXTENSIONS = TEXT_EDITABLE_EXTENSIONS | IMAGE_EXTENSIONS

# No other request-size limit exists anywhere in this stack (nginx.conf has
# no client_max_body_size).
MAX_IMAGE_UPLOAD_BYTES = 5 * 1024 * 1024


def decode_text_archives(archives: dict[str, bytes]) -> dict[str, str | bytes]:
    """Turns a raw-bytes archive dict into what AutomatonBuilder.build
    expects: `str` for text files (TEXT_EDITABLE_EXTENSIONS), untouched
    `bytes` for images — a text archive left undecoded would be wrapped wrong."""
    decoded: dict[str, str | bytes] = {}
    for name, content in archives.items():
        if Path(name).suffix.lower() in TEXT_EDITABLE_EXTENSIONS and isinstance(content, (bytes, bytearray)):
            decoded[name] = content.decode("utf-8")
        else:
            decoded[name] = content
    return decoded


_CSS_URL_PATTERN = re.compile(r"url\(\s*(['\"]?)([^'\")]+)\1\s*\)", re.IGNORECASE)
_ABSOLUTE_URL_PATTERN = re.compile(r"^(https?:)?//|^data:", re.IGNORECASE)


def css_referenced_basenames(css_text: str) -> set[str]:
    """Every relative `url(...)` target in `css_text`, reduced to its bare
    filename — a project's archive namespace is flat, so that's how a
    reference resolves. Absolute URLs (http(s)://, //, data:) are skipped:
    they don't name a project file at all."""
    names = set()
    for _, target in _CSS_URL_PATTERN.findall(css_text):
        target = target.strip()
        if not target or _ABSOLUTE_URL_PATTERN.match(target):
            continue
        names.add(Path(target).name)
    return names


def missing_css_references(css_text: str, known_archive_names: set[str]) -> list[str]:
    """Every name css_referenced_basenames(css_text) names that isn't in
    `known_archive_names` — order is whatever set iteration gives; the one
    caller sorts before display."""
    return [name for name in css_referenced_basenames(css_text) if name not in known_archive_names]


# @media/@supports are the only at-rules a chat-widget skin plausibly
# nests rules inside; @font-face/@page/@import etc. take a declaration
# list or no block at all, which parse_rule_list would misread as rules.
_NESTED_RULE_AT_RULES = frozenset({"media", "supports"})


def _collect_css_syntax_errors(nodes: list) -> list[str]:
    """Recurses through a parsed stylesheet/declaration-list/value's nodes,
    collecting every `error` tinycss2 attached anywhere in the tree —
    structural ones (a malformed rule or declaration) sit alongside their
    siblings; tokenization ones (an unterminated string/url) sit inside a
    declaration's own value, which is why this walks all the way down
    rather than stopping at the top level."""
    errors = []
    for node in nodes:
        node_type = getattr(node, "type", None)
        if node_type == "error":
            errors.append(f"line {node.source_line}: {node.message}")
        elif node_type == "qualified-rule" and node.content is not None:
            declarations = tinycss2.parse_declaration_list(node.content, skip_comments=True, skip_whitespace=True)
            errors.extend(_collect_css_syntax_errors(declarations))
        elif node_type == "at-rule" and node.content is not None and node.lower_at_keyword in _NESTED_RULE_AT_RULES:
            nested = tinycss2.parse_rule_list(node.content, skip_comments=True, skip_whitespace=True)
            errors.extend(_collect_css_syntax_errors(nested))
        elif node_type == "declaration":
            errors.extend(_collect_css_syntax_errors(node.value))
        elif node_type == "function":
            errors.extend(_collect_css_syntax_errors(node.arguments))
        elif node_type in ("() block", "[] block", "{} block"):
            errors.extend(_collect_css_syntax_errors(node.content))
    return errors


def css_syntax_errors(css_text: str) -> list[str]:
    """Every low-level syntax error tinycss2 finds in `css_text` — an
    unterminated string/block, a malformed selector or at-rule, a
    declaration missing its colon — as "line N: message" strings, empty if
    none. tinycss2 is a syntax-only (CSS Syntax Module) parser, not a full
    CSS engine: it won't flag a nonsense property value like `color: bees;`,
    only genuine malformation."""
    rules = tinycss2.parse_stylesheet(css_text, skip_comments=True, skip_whitespace=True)
    return _collect_css_syntax_errors(rules)


# Called with the newly-active Automaton once activate_project()/put_project()
# have committed it.
CommitCallback = Callable[[Automaton], Awaitable[None]]

# "New project" starts from this sample zip, resolved off this module's own
# location, not the cwd.
NEW_PROJECT_TEMPLATE = Path(__file__).resolve().parents[2] / "samples" / "projects" / "Hello world.zip"
NEW_PROJECT_NAME = "Hello world"


class ProjectService(object):
    def __init__(self, db: Db) -> None:
        self._db = db
        # Used only by export_project_zip/put_project's sessions.json handling.
        self._session_export_manager = SessionExportManager(db)
        self._session_import_manager = SessionImportManager(db)
        # (project_name, revision) -> Automaton. Revision-keyed so a caller
        # pinned to one specific revision and a caller wanting "whatever's
        # current" can share the cache without cross-serving.
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
        """Every *other* project's declared project.id mapped to its
        declared env key names, for AutomatonBuilder.build's automaton.*
        existence check."""
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
        """Drops every cached revision of `project_name`, for callers that
        can't tell which revisions are now stale. Ordinary edits go through
        _finalize_project_update instead, which re-caches just one revision."""
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
        recent draft, published or not. A caller needing a specific,
        possibly older revision uses _load_project_at_revision directly."""
        revision = self._db.get_project_revision(project_name)
        return self._load_project_at_revision(project_name, revision)

    def _project_update_changed(self, existing: Mapping[str, str | bytes], files: Mapping[str, str | bytes]) -> bool:
        """Whether `files` is a genuine change against `existing`. A file
        `existing` doesn't have yet always counts as changed, even with ""
        content — a brand-new empty file must still get persisted."""
        return any(name not in existing or existing[name] != content for name, content in files.items())

    def _prepare_project_update(
        self, project_name: str, files: Mapping[str, str | bytes]
    ) -> tuple[Automaton, dict[str, str | bytes] | None]:
        """Builds+validates the Automaton for `files` merged onto
        `project_name`'s current files. Read-only. Returns (automaton,
        to_persist), where to_persist is None if nothing actually changed."""
        existing = decode_text_archives(self._db.get_archives(project_name))
        merged = {**existing, **files}

        automaton = AutomatonBuilder().build(merged, self._known_projects_env_keys(project_name))
        self._validate_project_id_globally_unique(project_name, automaton.project_id)

        if not self._project_update_changed(existing, files):
            return automaton, None
        return automaton, merged

    def _validate_project_id_globally_unique(self, project_name: str, project_id: str | None) -> None:
        """The one project.id check AutomatonBuilder can't do itself:
        whether some *other* project has already claimed it. Raises before
        anything gets persisted, so a failed save leaves nothing partial."""
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
        extension = Path(file_name).suffix.lower()
        media_type = EXTENSION_TO_MEDIA_TYPE.get(extension, "application/octet-stream")
        # None for binary content — raw bytes aren't JSON-serializable; the
        # explorer renders those via the raw GET .../content route instead.
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
        """Called by every project-mutating path before awaiting `commit`.
        Refreshes the automaton cache and resets the active project's live
        conversation only when its current state no longer exists."""

        # Synced first: the reverse index below translates other projects'
        # project_id into project_name, so this project's own row must be
        # current before anything can resolve against it.
        self._db.set_project_metadata(
            project_name, automaton.project_id, automaton.project_ui_label, automaton.project_ui_description,
        )

        revision = self._db.get_project_revision(project_name)
        self._automaton_cache[(project_name, revision)] = automaton
        # Reverse index of every project this one's self-loop actions
        # reference via automaton.* — recomputed on every successful build
        # regardless of whether `project_name` is currently active.
        observed_project_names = self._resolve_automaton_project_refs(self._automaton_project_refs(automaton))
        self._db.set_project_observers(project_name, observed_project_names)
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
        """Every project_id `automaton`'s self-loop actions reference via
        automaton.* — raw tokens, not yet resolved to a project_name."""
        refs: set[str] = set()
        for state in automaton.states.values():
            for action in state.actions:
                if action.trigger and action.target == state.key:
                    refs |= trigger_automaton_project_refs(action.trigger)
        return refs

    def _resolve_automaton_project_refs(self, project_ids: set[str]) -> set[str]:
        """Translates automaton.* project_id tokens into project_name. A
        token matching no known project_id is silently dropped, not an
        error — a dangling reference is a runtime concern, not build-time."""
        names: set[str] = set()
        for project_id in project_ids:
            name = self._db.get_project_name_by_project_id(project_id)
            if name is not None:
                names.add(name)
        return names

    def recompute_availability(self, project_name: str) -> None:
        """Available exactly when the build succeeds and every automaton.*
        dependency is itself available. Writes only on change — this is
        what makes it safe to call from a cascade with no cycle detection."""
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
        """Subscribed once, for the process's lifetime. Recursive by
        construction: recompute_availability's write-only-on-change guard
        is what makes the cascade stop propagating on its own."""
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
        """'running' | 'paused' | 'manually_paused'. manually_paused
        always implies is_paused, so checking it first is enough to tell
        the two paused cases apart."""
        if manually_paused:
            return "manually_paused"
        if is_paused:
            return "paused"
        return "running"

    def get_runtime_status(self) -> list[dict]:
        """One row per project for the Settings > Runtime status view."""
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
        """Only allowed from 'running', enforced here rather than left to
        the UI alone. Recomputing afterward reuses the normal
        AvailabilityChanged cascade rather than a separate one."""
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
        """Only allowed from 'manually_paused'. Clears the flag and lets
        recompute_availability report the real state again, which may
        still be unavailable if a dependency went down in the meantime."""
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
        """One row, same shape as get_runtime_status — lets the
        pause/resume endpoints refresh just this row."""
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
        """Validates zip-slip safety, shape, and exactly one root
        'index.yml' — all before extracting anything. Two shapes are
        accepted: every file flat at the zip's root, or every file nested
        exactly one level inside a single common top-level folder (a
        common export shape, e.g. a GitHub download or drag-a-folder
        zip) — that folder is stripped, its contents imported as if
        they'd been flat all along. Anything deeper, or a mix of
        root-level files and a subdirectory, is rejected. Raises
        ValueError or zipfile.BadZipFile on any violation."""
        with zipfile.ZipFile(io.BytesIO(content)) as zf:
            names = [entry.replace("\\", "/") for entry in zf.namelist()]
            # macOS's Finder/Archive Utility tacks on a __MACOSX/ sidecar
            # folder full of resource-fork metadata (AppleDouble ._filename
            # entries, nothing of actual interest) whenever it zips a
            # folder — ignored outright rather than counted as a second
            # top-level folder, so a Mac-zipped single-folder export still
            # gets descended into instead of rejected.
            names = [n for n in names if n.split("/", 1)[0] != "__MACOSX"]

            for name in names:
                if name.startswith("/") or any(part == ".." for part in Path(name).parts):
                    raise ValueError(f"Unsafe path inside zip: '{name}'.")

            # A pure directory entry (name ending in '/') carries no file
            # of its own — irrelevant to both the shape check and
            # extraction, whether or not the zip tool bothered to include one.
            file_names = [n for n in names if not n.endswith("/")]
            flat_names = [n for n in file_names if "/" not in n]
            top_level_dirs = {n.split("/", 1)[0] for n in file_names if "/" in n}

            if flat_names and top_level_dirs:
                raise ValueError(
                    "Zip must be either flat (no subdirectories) or a single folder containing "
                    "everything — found both root-level file(s) and a subdirectory."
                )
            if len(top_level_dirs) > 1:
                raise ValueError(
                    f"Zip must be flat or contain a single top-level folder — found multiple: "
                    f"{', '.join(sorted(top_level_dirs))}."
                )

            prefix = f"{next(iter(top_level_dirs))}/" if top_level_dirs else ""
            # original name -> effective (prefix-stripped) name.
            effective = {n: n[len(prefix):] for n in file_names}

            staging_resolved = staging_dir.resolve()
            for original, stripped in effective.items():
                if not stripped or "/" in stripped:
                    raise ValueError(f"Zip must be flat (no subdirectories): found '{original}'.")
                resolved = (staging_dir / stripped).resolve()
                if resolved != staging_resolved and staging_resolved not in resolved.parents:
                    raise ValueError(f"Unsafe path inside zip: '{original}'.")

            index_entries = [s for s in effective.values() if s == "index.yml"]
            other_yaml_entries = [
                s for s in effective.values() if s != "index.yml" and s.lower().endswith((".yml", ".yaml"))
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

            for original, stripped in effective.items():
                target = staging_dir / stripped
                target.parent.mkdir(parents=True, exist_ok=True)
                with zf.open(original) as src, open(target, "wb") as dst:
                    dst.write(src.read())

    def get_active_project_name(self) -> str:
        """The current session user's active project name, read fresh from
        the DB every time. Raises if nothing is active, e.g. never
        activated anything or the active project was since deleted."""
        name = self._db.get_active_project_name(Session().user)
        if name is None:
            raise FileNotFoundError("No project is currently active.")
        return name

    def get_project_availability(self, project_name: str) -> tuple[bool, str | None]:
        """(is_paused, paused_reason). Returns (False, None), never
        raises, for a project that doesn't exist at all."""
        return self._db.get_project_availability(project_name) or (False, None)

    def _resolve_state(self, project_name: str, automaton: Automaton) -> State:
        """No persisted state yet falls back to init_action.target. A
        persisted state that no longer exists means a publish renamed or
        removed it — only StateRemap (written at that publish) may resolve it."""
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
        current State — never the in-progress draft. Raises ValueError
        when `project_name` has no published revision yet."""
        published_revision = self._db.get_project_published_revision(project_name)
        if published_revision is None:
            raise ValueError(f"Project '{project_name}' has never been published.")
        automaton = self._load_project_at_revision(project_name, published_revision)
        return automaton, self._resolve_state(project_name, automaton)

    def get_active_automaton_and_state(self) -> tuple[Automaton, State]:
        """The active project's published automaton and state — never the
        in-progress draft. A caller with a concrete session_id uses
        get_automaton_and_state_for_session instead."""
        project_name = self.get_active_project_name()
        if project_name is None:
            raise FileNotFoundError("No project is currently active.")
        return self.get_automaton_and_state(project_name)

    def get_automaton_and_state_for_session(self, session_id: int) -> tuple[Automaton, State]:
        """The Automaton `session_id`'s turns must run against. A native
        session is pinned to the revision published when it was created;
        a 'test' session always re-resolves against the live draft."""
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
        """`project_name`'s published Automaton and State, as seen by
        `username`. Returns None, never raises, when `username` has no
        session — unlike a nonexistent `project_name`, which raises FileNotFoundError."""
        if not self._db.project_exists(project_name):
            raise FileNotFoundError(f"Project '{project_name}' does not exist.")
        session = self._db.get_latest_chat_session(username, project_name)
        if session is None:
            return None
        automaton = self._load_project_at_revision(project_name, session["project_revision"])
        return automaton, self._resolve_state(project_name, automaton)

    def get_draft_automaton_and_state(self, project_name: str) -> tuple[Automaton, State]:
        """Like get_automaton_and_state, but the in-progress draft rather
        than published-only — needed so a "Test" session stays creatable
        against a project that's never been published yet. Takes
        `project_name` explicitly (the embedded "Test" chat's own URL
        already carries it) rather than resolving it off the
        active-project pointer — that pointer is keyed per Session().user
        and can easily be unset or pointing elsewhere for whoever's
        making this call, even though the URL already says exactly which
        project this is about."""
        automaton = self._load_project(project_name)
        return automaton, self._resolve_state(project_name, automaton)

    def apply_manual_action(self, action_name: str, session_id: int) -> tuple[StatePayload, Action, str]:
        """Applies a manual (button) action and returns the destination
        state's payload, the Action that fired, and the source state's
        key (e.g. to detect a self-loop)."""
        automaton, state = self.get_automaton_and_state_for_session(session_id)
        action = automaton.move(state.key, action_name)
        new_state = automaton.get_state(action.target)
        # Always saved, self-loop or not: a self-loop just never counts
        # toward history_cutoff.
        self._db.save_transition(
            state.key,
            action_name,
            new_state.key,
            session_id,
            transition_log_level=new_state.transition_log_level,
        )
        # This path writes save_transition directly rather than going through
        # TrackingEngine.apply_transition, so it must publish explicitly.
        session = self._db.get_chat_session(session_id)
        assert session is not None  # already resolved by get_automaton_and_state_for_session above
        TrackingEngine.notify_transition(session["username"], session["project_name"], state.key, new_state.key)
        return automaton.get_state_payload(new_state), action, state.key

    def get_active_state_payload(self) -> StatePayload:
        automaton, state = self.get_active_automaton_and_state()
        return automaton.get_state_payload(state)

    def reset_active_project(self) -> None:
        # User-scoped: wipes only the current user's own sessions/messages/
        # signals, not every user's, unlike delete_project's reset_project.
        self._db.reset_project_for_user(Session().user, self.get_active_project_name())

    def _resolve_inspector_revision(self, project_name: str, session_id: int | None) -> int:
        """The revision an Inspect-panel read should read `project_name`
        at. Mirrors get_automaton_and_state_for_session's own resolution,
        so reviewing an older session never shows today's structure."""
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
        """Signal definitions of `project_name`'s index.yml, for the
        Inspect panel. `relevant` scopes to `state_key`'s outgoing
        actions when given, or every state's triggers combined otherwise."""
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
                # Not part of SignalPayload itself — filenames only, never
                # full content.
                "attachments": [a.filename for a in signal.attachments.values()],
            }
            for signal in automaton.signals
        ]

    def get_project_env_keys(self, project_name: str, session_id: int | None = None) -> list[dict]:
        """Env-key declarations of `project_name`'s index.yml, for the
        Inspect panel Env tab — same revision contract as get_project_signals."""
        automaton = self._load_project_at_revision(
            project_name, self._resolve_inspector_revision(project_name, session_id)
        )
        return [{"env_key": Automaton.get_env_key_payload(env_key)} for env_key in automaton.env_keys]

    def get_project_metadata(self, project_name: str) -> ProjectPayload:
        """The optional top-level `project:` section of `project_name`'s
        last saved index.yml, read off the already-built Automaton rather
        than re-parsing the YAML text."""
        automaton = self._load_project(project_name)
        return {
            "id": automaton.project_id,
            "ui_label": automaton.project_ui_label,
            "ui_description": automaton.project_ui_description,
        }

    def get_identifier_registry(self, project_name: str) -> dict[str, dict[str, str]]:
        """Every identifier `project_name`'s trigger/`env:` expressions can
        reference, plus an "automaton.<id>"/"automaton.<id>.env" entry per
        *other* project with a project.id."""
        automaton, _ = self.get_automaton_and_state(project_name)
        registry = build_registry(automaton.signals, automaton.env_keys)
        registry["automaton"] = {}
        for name in self._db.list_projects():
            if name == project_name:
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
        automaton, excluding the reserved "" pseudo-state."""
        automaton = self._load_project(project_name)
        return [state.key for state in automaton.states.values() if state.key != ""]

    def get_project_graph(self, project_name: str, session_id: int | None = None) -> dict:
        """The project's state machine as nodes (states) and edges
        (actions). The reserved "" state is excluded from `nodes` but
        `edges` still includes its init_action as a `source: ""` edge."""
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
                # Not part of StatePayload — a state's system-prompt text
                # never reaches a live chat client, only this Inspect panel.
                "contextual_prompt": state.contextual_prompt,
            }
            for state in real_states
        ]
        edges = [
            {
                "action": Automaton.get_action_payload(action),
                "source": state.key,
                # None of these three belong in ActionPayload — `trigger`
                # especially never reaches a live chat client.
                "trigger": action.trigger,
                "action_prompt": action.action_prompt,
                "ui_description": action.ui_description,
            }
            for state in automaton.states.values()
            for action in state.actions
        ]
        return {
            "nodes": nodes, "edges": edges, "autotracking_on_ai_message": automaton.autotracking_on_ai_message,
            # The exact revision this graph was actually built from — lets
            # the "Rev. X" badge stay accurate without a second fetch.
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
        """{revision, published_revision, is_paused, paused_reason} for the
        "Edit project" toolbar's revision display, refreshed after every
        save and publish."""
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
        """Whether publishing `project_name` needs a human state remap
        decision: the current persisted state has gone missing from the
        draft about to be published. Also reports `has_active_sessions`."""
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
        """Sets published_revision = revision, freezing the current draft.
        If the persisted state has gone missing from it, `remap_to` must
        name a real state; the StateRemap written is consulted from then on."""
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
        whatever was last published."""
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
        archive, or a single bare YAML file treated as index.yml's own
        content with no attachments."""

        if not self._is_safe_project_name(project_name):
            raise ValueError(f"Invalid project name: '{project_name}'.")

        try:
            if self._looks_like_zip(content_type, content):
                with tempfile.TemporaryDirectory() as tmp:
                    staging_dir = Path(tmp)
                    self._extract_zip_safely(content, staging_dir)
                    # Everything export_project_zip can produce is UTF-8 text
                    # except image assets — read_text() on those (e.g. a PNG's
                    # magic bytes) raised a UnicodeDecodeError, so import/export
                    # was never actually round-trippable for a project with
                    # any Theme asset in it.
                    files = {
                        file.name: (
                            file.read_bytes() if file.suffix.lower() in IMAGE_EXTENSIONS
                            else file.read_text(encoding="utf-8")
                        )
                        for file in staging_dir.iterdir()
                    }
            else:
                files = {"index.yml": content.decode("utf-8")}
            # Pulled out before AutomatonBuilder sees `files`; a malformed
            # sessions.json fails the whole upload rather than partially
            # succeeding. Actually imported only once the project commits below.
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
            # This upload path is text-only; content_type is inferred from
            # each entry's own extension, same as put_project_file.
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
        """None -> []. Otherwise must be a JSON array of session objects;
        anything else raises ValueError."""
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
        """Best-effort, one session at a time: a malformed entry is
        skipped and logged rather than blocking the rest. Publishes
        `project_name` first, since every ChatSession needs a published revision."""
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
        """`base` itself if free, else the first "`base` N" (N starting
        at 2) not already in use."""
        existing = set(self._db.list_projects())
        if base not in existing:
            return base
        suffix = 2
        while f"{base} {suffix}" in existing:
            suffix += 1
        return f"{base} {suffix}"

    async def create_new_project(self, commit: CommitCallback) -> dict:
        """Creates a project from NEW_PROJECT_TEMPLATE, going through
        put_project so validation/staging/commit stay identical to a
        real upload."""
        content = NEW_PROJECT_TEMPLATE.read_bytes()
        project_name = self._unique_project_name(NEW_PROJECT_NAME)
        return await self.put_project(project_name, content, "application/zip", commit)

    def export_project_zip(self, project_name: str) -> bytes:
        """`project_name`'s files, round-trippable back through
        put_project, plus a SESSIONS_EXPORT_FILENAME holding every
        *imported* session, omitted when there are none."""
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
        """A flat, non-hidden file name (no path traversal) with one of
        EDITABLE_EXTENSIONS. Anything else stays out of scope."""
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
        """Every text-editable file in `project_name`, for the file
        explorer panel. index.yml sorts first, then the rest alphabetically."""

        names = self._db.list_archives(project_name)
        names.sort(key=lambda name: (name != "index.yml", name))
        logger.critical(names)
        return names

    def get_project_file(self, project_name: str, file_name: str) -> dict:
        """{content, can_undo, can_redo} for `file_name`'s current
        content, scoped to the current user."""
        return self._file_undo_redo_info(project_name, file_name)

    def get_project_file_content(
        self, project_name: str, file_name: str, session_id: int | None
    ) -> tuple[bytes, str]:
        """Raw (content, content_type) for `file_name` — bytes aren't
        JSON-serializable, so this exists separately from get_project_file.
        `session_id` resolves via _resolve_inspector_revision. index.css's
        own url(...) references are left exactly as written — resolving
        them into fetchable URLs is the frontend's job (see
        cssAssetUrls.js's resolveCssAssetUrls, applied client-side by both
        ChatPreview.vue and chatStore.js's loadSkin): this endpoint has no
        way to know what origin the page injecting the result actually
        runs on relative to the API, and a relative /api/... path this
        raw text would otherwise get rewritten to only happens to resolve
        correctly in production, where nginx proxies the frontend and API
        onto the same origin — not in dev, where they're on two different
        ports with no proxy between them."""
        revision = self._resolve_inspector_revision(project_name, session_id)
        content = self._db.get_archive(project_name, file_name, revision=revision)
        if content is None:
            raise FileNotFoundError(f"File '{file_name}' does not exist in project '{project_name}'.")
        content_type = self._db.get_archive_content_type(project_name, file_name, revision=revision)
        assert content_type is not None  # same Archive row get_archive already found content for
        return content, content_type

    async def put_project_file(
        self, project_name: str, file_name: str, content: bytes | str, content_type_header: str | None,
        commit: CommitCallback,
    ) -> dict:
        """Creates or edits one of `project_name`'s files in place. A text
        extension is decoded as UTF-8, content_type inferred from the
        extension. An image extension requires a matching `content_type_header`."""
        if project_name not in self._db.list_projects():
            raise FileNotFoundError(f"Project '{project_name}' does not exist.")

        self._check_editable_file_name(file_name)
        extension = Path(file_name).suffix.lower()

        if extension in TEXT_EDITABLE_EXTENSIONS:
            text_content = content.decode("utf-8") if isinstance(content, bytes) else content
            content_type = TEXT_CONTENT_TYPE_BY_EXTENSION.get(extension, "text/plain")
            if file_name == "index.css":
                syntax_errors = css_syntax_errors(text_content)
                if syntax_errors:
                    raise ValueError(
                        f"index.css has invalid syntax: {'; '.join(syntax_errors)}."
                    )
                known_names = set(self._db.list_archives(project_name)) | {file_name}
                missing = missing_css_references(text_content, known_names)
                if missing:
                    raise ValueError(
                        f"index.css references missing file(s): {', '.join(sorted(missing))}."
                    )
            update_value: str | bytes = text_content
            to_save: bytes = text_content.encode("utf-8")
        else:
            # Only a text extension ever hands this a `str`; an image
            # upload is always real bytes off the request body.
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
        `project_name`'s index.yml text, persists it via put_project_file,
        and returns `operation`'s own result untouched."""
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
        """A pure editor preview, not a persisted change — never touches
        Archive or the automaton cache. `content` is the editor's current
        unsaved state, kept so a later redo can restore it."""
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
        """Deletes the current user's undo/redo history for every file
        in `project_name`, so a fresh editing session starts clean."""
        if project_name not in self._db.list_projects():
            raise FileNotFoundError(f"Project '{project_name}' does not exist.")
        self._db.clear_history(Session().user, project_name)

    async def delete_project_file(
        self, project_name: str, file_name: str, commit: CommitCallback
    ) -> None:
        """Deleting index.css cascades to every image asset it could have
        referenced — the file explorer's own "Theme" branch never offers
        deleting one of those individually while index.css still exists, so
        an orphaned asset would otherwise just be dead weight. Deleting an
        asset index.css still references is rejected outright instead:
        editing index.css's own text to drop the reference is exactly what
        the CSS editor is for, and rewriting it here on the asset's behalf
        risks mangling a rule irrecoverably for a small convenience."""

        if project_name not in self._db.list_projects():
            raise FileNotFoundError(f"Project '{project_name}' does not exist.")

        archives = self._db.get_archives(project_name=project_name)
        if file_name != "index.css" and Path(file_name).suffix.lower() in IMAGE_EXTENSIONS:
            index_css = archives.get("index.css")
            if index_css is not None and file_name in css_referenced_basenames(index_css.decode("utf-8")):
                raise ValueError(
                    f"'{file_name}' is still referenced by index.css — remove the reference "
                    f"there first (or delete index.css itself, which takes its assets with it)."
                )

        try:
            del archives[file_name]
            cascade_names = (
                [name for name in archives if Path(name).suffix.lower() in IMAGE_EXTENSIONS]
                if file_name == "index.css" else []
            )
            for name in cascade_names:
                del archives[name]
            new_automaton = AutomatonBuilder().build(archives, self._known_projects_env_keys(project_name))
        except Exception as exc:
            raise ValueError(f"Invalid project definition: {exc}") from exc

        self._db.delete_archive(project_name, file_name)
        for name in cascade_names:
            self._db.delete_archive(project_name, name)
        await self._finalize_project_update(project_name, new_automaton, commit)

    async def delete_project(self, project_name: str, commit: CommitCallback) -> None:
        self._db.reset_project(project_name)
        self._db.delete_archives(project_name)
        self._invalidate_automaton_cache(project_name)

        if project_name == self.get_active_project_name():
            # Falls back to whatever's left, or nothing at all (the
            # "select a project" empty state) if that was the last one.
            remaining = self._db.list_projects()
            fallback = next(iter(remaining), None)
            if fallback is not None:
                await self.activate_project(fallback, commit)
            else:
                self._db.clear_active_project_name(Session().user)
