from __future__ import annotations

import logging
from pathlib import Path

from automaton.automaton import ActionPayload, Automaton, EnvKeyPayload, ProjectPayload, SignalPayload, StatePayload
from automaton.automaton_builder import AutomatonBuilder, EXTENSION_TO_MEDIA_TYPE
from automaton.automaton_yaml_editor import AutomatonYamlEditor
from db import Db
from session import Session

from .inspector import ProjectInspector
from .manager import ProjectManager
from .parsers import AutomatonLoader, css_referenced_basenames, css_syntax_errors, missing_css_references
from .parsers import (
    EDITABLE_EXTENSIONS, IMAGE_CONTENT_TYPE_BY_EXTENSION, IMAGE_EXTENSIONS, MAX_IMAGE_UPLOAD_BYTES,
    TEXT_CONTENT_TYPE_BY_EXTENSION, TEXT_EDITABLE_EXTENSIONS,
)
from .types import CommitCallback

logger = logging.getLogger(__name__)


class ProjectEditor:
    def __init__(
        self, db: Db, automaton_loader: AutomatonLoader, inspector: ProjectInspector, manager: ProjectManager,
    ) -> None:
        self._db = db
        self._automaton_loader = automaton_loader
        self._inspector = inspector
        self._manager = manager

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
        revision = self._inspector._resolve_inspector_revision(project_name, session_id)
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
            new_automaton, to_persist = self._manager.prepare_update(project_name, {file_name: update_value})
        except Exception as exc:
            raise ValueError(f"Invalid project update: {exc}") from exc

        if to_persist is not None:
            self._db.save_project_file(Session().user, project_name, file_name, to_save, content_type)
        await self._manager.finalize_update(project_name, new_automaton, commit)

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
            new_automaton = AutomatonBuilder().build(archives, self._automaton_loader.known_projects_env_keys(project_name))
        except Exception as exc:
            raise ValueError(f"Invalid project definition: {exc}") from exc

        self._db.delete_archive(project_name, file_name)
        for name in cascade_names:
            self._db.delete_archive(project_name, name)
        await self._manager.finalize_update(project_name, new_automaton, commit)
