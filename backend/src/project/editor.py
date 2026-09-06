from __future__ import annotations

import re
from pathlib import Path
from typing import TYPE_CHECKING

from automaton.automaton import (
    ActionPayload, EnvKeyPayload, ProjectPayload, SignalPayload, SourcePayload, StatePayload,
)
from automaton.builder.archive_resolver import EXTENSION_TO_MEDIA_TYPE
from automaton.automaton_builder import AutomatonBuilder
from automaton.build_error import AutomatonBuildError
from automaton.automaton_yaml_editor import AutomatonYamlEditor
from db import ContentRestored, Db, FileRenamed
from logging_factory import LoggerFactory
from session import Session

from .inspector import ProjectInspector
from .manager import ProjectManager
from .archive.automaton_loader import AutomatonLoader
from .archive.css_validator import CssValidator
from .archive.layout import (
    ASPECT_DIR, BEHAVIOUR_DIR, ArchiveLayout, IMAGE_CONTENT_TYPE_BY_EXTENSION, LEGAL_TERMS_FILE_NAME,
    LEGAL_TERMS_SKELETON, MAX_IMAGE_UPLOAD_BYTES, ROOT_FILE_NAMES, SOURCES_DIR, TEXT_CONTENT_TYPE_BY_EXTENSION,
    TEXT_EDITABLE_EXTENSIONS,
)
from .types import CommitCallback

if TYPE_CHECKING:
    from ai import AiService

logger = LoggerFactory.get_logger(__name__)

# backend/src/docs/ — same directory ChatController.get_doc serves
# PROJECT_SPECS.md from (as "project-specs"); read directly here rather
# than going through that endpoint since this runs server-side.
DOCS_DIR = Path(__file__).resolve().parent.parent / "docs"

# Both AI-edit prompts below share this placeholder for the format spec's
# own text, substituted with .replace() rather than str.format() — the
# spec/CSS bodies are full of literal `{`/`}` characters that would
# otherwise have to be escaped throughout.
_SPEC_PLACEHOLDER = "%%SPEC%%"

INDEX_YML_AI_EDIT_SYSTEM_PROMPT = """\
You are editing the `index.yml` file of an Avance project — a YAML file \
that defines a state-machine driving an AI chat conversation. Follow the \
format specification below exactly; every rule it states is enforced by \
the backend at upload time, so an invalid file is rejected outright.

%%SPEC%%

You will be given this project's current `index.yml`, the basenames of \
attachment files already uploaded under `behaviour/` (if any), and a \
description of a problem to solve or change to make. Reply with the \
complete new `index.yml` content that addresses it — the whole file, not \
a diff or an excerpt, keeping everything unrelated to the request unchanged.

Getting this exactly right matters more than anything else: a file that \
fails to parse or fails validation is worse than no change at all.
- The result must be strictly valid YAML, parseable start to finish. Every \
quoted string you open must be closed — never leave a `"` or `'` dangling.
- For any string value that spans multiple lines, contains a quote \
character, or is otherwise awkward to quote, use a YAML block scalar \
(`|` for literal, `>` for folded) instead of a quoted flow scalar — it \
needs no escaping and cannot produce an unbalanced-quote error.
- Every piece of text in this file — `contextual-prompt`, `fixed-message`, \
`ui-label`, `ui-description`, any of it — is plain prose or, where the \
specification says so, Markdown. Never HTML: no `<div>`, `<span>`, or any \
other markup tag anywhere in the file, including inside prompt/description \
strings.
- Only use fields, keys, and value shapes the specification above actually \
defines — never invent one.
- An `attachments:` entry (global, per-signal, per-state) or a `sources:` \
entry's own `url: avance:<path>` must name a file already listed below as \
uploaded — never invent one. If the change calls for an attachment or \
source that isn't listed, leave that part out (or note what's needed in a \
comment) rather than pointing at a file that doesn't exist.
- Before you answer, re-check the whole file in your head against the \
specification's own §9 validation checklist, and fix anything that would \
fail it.

Reply with nothing but the YAML itself, inside a single ```yaml code fence \
— no explanation before or after it.\
"""

INDEX_CSS_AI_EDIT_SYSTEM_PROMPT = """\
You are editing the `index.css` file of an Avance project — the stylesheet \
that skins its chat widget. Follow the format specification below exactly; \
every rule it states is enforced by the backend at save time, so an \
invalid file is rejected outright.

%%SPEC%%

You will be given this project's current `index.css`, the basenames of \
image assets already uploaded under `aspect/` (if any), and a description \
of a problem to solve or change to make. Reply with the complete new \
`index.css` content that addresses it — the whole file, not a diff or an \
excerpt, keeping everything unrelated to the request unchanged.

Getting this exactly right matters more than anything else: a file that \
fails to parse or fails validation is worse than no change at all.
- Write plain CSS3 only — no SCSS/LESS syntax, no nesting other than what \
@media/@supports blocks themselves give you.
- The result must be syntactically valid CSS, parseable start to finish: \
every brace block you open must be closed, and every quoted string (inside \
content:"...", url("..."), etc.) must be closed on the line it opens on — \
never leave a brace or a quote dangling.
- Never write HTML, Markdown, or any other non-CSS content anywhere in the \
file. A CSS comment (/* ... */) is fine; anything else outside CSS syntax \
is not.
- Reference an image only as url("basename.ext"), and only a basename \
already listed below as uploaded — never invent one. If the change calls \
for an image that isn't listed, leave that part out (or note what's \
needed in a CSS comment) rather than pointing at a file that doesn't exist.
- Only rely on the selectors the specification's own §4 lists as a stable \
hook where the request depends on them actually being visible — nothing \
else in the chat UI is guaranteed to render your rule.
- Before you answer, re-check the whole file in your head against the \
specification's own §8 checklist, and fix anything that would fail it.

Reply with nothing but the CSS itself, inside a single ```css code fence \
— no explanation before or after it.\
"""

# Pulls the body out of a fenced code block (```css, ```yaml, or bare
# ```) — models reliably wrap their output in one despite the system
# prompt asking for "nothing but the CSS/YAML", so this is the normal
# path, not a fallback.
_CODE_FENCE_RE = re.compile(r"```[a-zA-Z0-9_+-]*\s*\n(.*?)```", re.DOTALL)


class ProjectEditor:
    def __init__(
        self, db: Db, automaton_loader: AutomatonLoader, inspector: ProjectInspector, manager: ProjectManager,
        ai_service: "AiService | None" = None,
    ) -> None:
        self._db = db
        self._automaton_loader = automaton_loader
        self._inspector = inspector
        self._manager = manager
        # Optional: only needed for generate_index_yml_ai_edit — every
        # other method here works without it.
        self._ai_service = ai_service

    def _resolve_file_name(self, project_id: str, file_name: str, revision: int | None = None) -> str:
        names = self._db.list_archives(project_id, revision=revision)
        if file_name in names:
            return file_name
        matches = [name for name in names if Path(name).name == file_name]
        return matches[0] if len(matches) == 1 else file_name

    def _file_undo_redo_info(self, project_id: str, file_name: str) -> dict:
        file_name = self._resolve_file_name(project_id, file_name)
        content = self._db.get_archive(project_id, file_name)
        if content is None:
            raise FileNotFoundError(f"File '{file_name}' does not exist in project '{project_id}'.")
        content_type = self._db.get_archive_content_type(project_id, file_name)
        user = Session().user
        extension = Path(file_name).suffix.lower()
        media_type = EXTENSION_TO_MEDIA_TYPE.get(extension, "application/octet-stream")
        # None for binary content — raw bytes aren't JSON-serializable; the
        # explorer renders those via the raw GET .../content route instead.
        is_text = extension in TEXT_EDITABLE_EXTENSIONS
        return {
            "content": content.decode("utf-8") if is_text else None,
            "can_undo": self._db.has_undo(user, project_id, file_name),
            "can_redo": self._db.has_redo(user, project_id, file_name),
            "content_type": content_type,
            "media_type": media_type,
            "size": len(content),
        }

    def list_project_files(self, project_id: str) -> list[str]:
        """Every text-editable file in `project_id`, for the file
        explorer panel. index.yml sorts first, then the rest alphabetically."""

        names = self._db.list_archives(project_id)
        names.sort(key=lambda name: (name != "index.yml", name))
        return names

    def get_project_file(self, project_id: str, file_name: str) -> dict:
        """{content, can_undo, can_redo} for `file_name`'s current
        content, scoped to the current user."""
        return self._file_undo_redo_info(project_id, file_name)

    async def _run_ai_edit(self, system_prompt_template: str, spec_file_name: str, user_turn: str) -> str:
        """Shared by generate_index_yml_ai_edit/generate_index_css_ai_edit
        below: fills `system_prompt_template`'s %%SPEC%% placeholder with
        `spec_file_name`'s own text, sends `user_turn` as the one user
        message, and pulls the new file's content out of the reply's own
        fenced code block."""
        if self._ai_service is None:
            raise ValueError("No AiService is configured for this deployment.")
        spec = (DOCS_DIR / spec_file_name).read_text(encoding="utf-8")
        system_prompt = system_prompt_template.replace(_SPEC_PLACEHOLDER, spec)
        reply = await self._ai_service.generate(system_prompt, [{"role": "user", "content": user_turn}])
        match = _CODE_FENCE_RE.search(reply)
        return (match.group(1) if match else reply).strip() + "\n"

    async def generate_index_yml_ai_edit(self, project_id: str, instruction: str) -> str:
        """Backs the "Edit project" index.yml editor's AI button
        (IndexYmlEditorPanel.vue): sends the AiService a prompt built from
        the format spec (PROJECT_SPECS.md), this project's current
        index.yml, the basenames of its own already-uploaded `behaviour/`
        attachments (so the model never invents an `attachments:` entry or
        a `sources:` entry's own `url: avance:behaviour/...` pointing at a
        file that doesn't exist), and `instruction` describing the change
        to make, and returns the
        new index.yml content it replies with. A pure preview, same as
        undo/redo above — nothing is persisted here, the frontend drops
        the result into its own (unsaved) editor buffer."""
        content = self._db.get_archive(project_id, "index.yml")
        if content is None:
            raise FileNotFoundError(f"Project '{project_id}' has no index.yml.")
        existing_names = self._db.list_archives(project_id)
        attachment_names = sorted(Path(name).name for name in existing_names if name.startswith(f"{BEHAVIOUR_DIR}/"))
        attachments_line = ", ".join(attachment_names) if attachment_names else "(none uploaded yet)"
        user_turn = (
            f"Current index.yml:\n```yaml\n{content.decode('utf-8')}\n```\n\n"
            f"Attachments already uploaded under behaviour/: {attachments_line}\n\n"
            f"Requested change:\n{instruction}"
        )
        return await self._run_ai_edit(INDEX_YML_AI_EDIT_SYSTEM_PROMPT, "PROJECT_SPECS.md", user_turn)

    async def generate_index_css_ai_edit(self, project_id: str, instruction: str) -> str:
        """Backs the "Edit project" index.css (Aspect) editor's AI button
        (IndexCssEditorPanel.vue) — same shape as generate_index_yml_ai_edit
        above, built from the skin format spec (SKIN_SPECS.md), this
        project's current index.css, the basenames of its own already-
        uploaded `aspect/` assets (so the model never invents a `url(...)`
        reference to a file that doesn't exist), and `instruction`. A pure
        preview, nothing persisted here — see generate_index_yml_ai_edit."""
        content = self._db.get_archive(project_id, "index.css")
        if content is None:
            raise FileNotFoundError(f"Project '{project_id}' has no index.css.")
        existing_names = self._db.list_archives(project_id)
        asset_names = sorted(Path(name).name for name in existing_names if name.startswith(f"{ASPECT_DIR}/"))
        assets_line = ", ".join(asset_names) if asset_names else "(none uploaded yet)"
        user_turn = (
            f"Current index.css:\n```css\n{content.decode('utf-8')}\n```\n\n"
            f"Assets already uploaded under aspect/: {assets_line}\n\n"
            f"Requested change:\n{instruction}"
        )
        return await self._run_ai_edit(INDEX_CSS_AI_EDIT_SYSTEM_PROMPT, "SKIN_SPECS.md", user_turn)

    def get_project_file_content(
        self, project_id: str, file_name: str, session_id: int | None
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
        revision = self._inspector._resolve_inspector_revision(project_id, session_id)
        return self._get_file_content_at_revision(project_id, file_name, revision)

    def get_project_file_content_at_revision(self, project_id: str, file_name: str, revision: int) -> tuple[bytes, str]:
        return self._get_file_content_at_revision(project_id, file_name, revision)

    def _get_file_content_at_revision(self, project_id: str, file_name: str, revision: int) -> tuple[bytes, str]:
        file_name = self._resolve_file_name(project_id, file_name, revision)
        content = self._db.get_archive(project_id, file_name, revision=revision)
        if content is None:
            raise FileNotFoundError(f"File '{file_name}' does not exist in project '{project_id}'.")
        content_type = self._db.get_archive_content_type(project_id, file_name, revision=revision)
        assert content_type is not None  # same Archive row get_archive already found content for
        return content, content_type

    async def put_project_file(
        self, project_id: str, file_name: str, content: bytes | str, content_type_header: str | None,
        commit: CommitCallback,
    ) -> dict:
        """Creates or edits one of `project_id`'s files in place. A text
        extension is decoded as UTF-8, content_type inferred from the
        extension. An image extension requires a matching `content_type_header`."""
        if project_id not in self._db.list_projects():
            raise FileNotFoundError(f"Project '{project_id}' does not exist.")

        existing_names = self._db.list_archives(project_id)
        resolved_name = self._resolve_file_name(project_id, file_name)
        if resolved_name not in existing_names:
            self._check_editable_file_name(file_name)
        file_name = resolved_name
        extension = Path(file_name).suffix.lower()

        if extension in TEXT_EDITABLE_EXTENSIONS:
            text_content = content.decode("utf-8") if isinstance(content, bytes) else content
            content_type = TEXT_CONTENT_TYPE_BY_EXTENSION.get(extension, "text/plain")
            if file_name == "index.css":
                syntax_errors = CssValidator.syntax_errors(text_content)
                if syntax_errors:
                    raise ValueError(
                        f"index.css has invalid syntax: {'; '.join(syntax_errors)}."
                    )
                known_names = {Path(name).name for name in existing_names if name.startswith(f"{ASPECT_DIR}/")}
                missing = CssValidator.missing_references(text_content, known_names)
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

        # Read before prepare_update/save touch anything — the only way to
        # later tell a family-only edit (project.id unchanged) apart from
        # any other save, so finalize_update knows to re-run the dependents
        # rescan (see its own old_family parameter).
        old_family = self._automaton_loader.declared_family(project_id)
        try:
            new_automaton, to_persist = self._manager.prepare_update(project_id, {file_name: update_value})
        except AutomatonBuildError:
            raise
        except Exception as exc:
            raise ValueError(f"Invalid project update: {exc}") from exc

        if to_persist is not None:
            self._db.save_project_file(Session().user, project_id, file_name, to_save, content_type)
        project_id = await self._manager.finalize_update(project_id, new_automaton, commit, old_family=old_family)

        return {"success": True, "project_id": project_id, **self._file_undo_redo_info(project_id, file_name)}

    async def rename_project_file(self, project_id: str, old_name: str, new_name: str, commit: CommitCallback) -> dict:
        """Renames one file within the current draft revision, keeping its
        content and its own category (aspect/behaviour) unchanged — only
        the basename is user-editable, same as an upload's target name is
        derived from its extension, never a free path. Auto-rewrites any
        literal occurrence of the old basename in index.yml (attachments:,
        a `sources:` entry's own `url: avance:behaviour/...`) and index.css
        (url(...)) so the rename can never leave a dangling reference behind; both
        are just plain text at this level, so one substring replace covers
        every reference syntax. index.yml/index.css/legal/terms.md — fixed
        names the rest of the system assumes exist exactly as spelled —
        can never be the file being renamed."""
        if project_id not in self._db.list_projects():
            raise FileNotFoundError(f"Project '{project_id}' does not exist.")
        existing_names = self._db.list_archives(project_id)
        old_name = self._resolve_file_name(project_id, old_name)
        if old_name not in existing_names:
            raise FileNotFoundError(f"File '{old_name}' does not exist in project '{project_id}'.")
        if old_name in ROOT_FILE_NAMES or old_name == LEGAL_TERMS_FILE_NAME:
            raise ValueError(f"'{old_name}' can't be renamed.")

        old_basename = Path(old_name).name
        new_basename = new_name.strip()
        # A plain file name only — never a path. Rejected outright rather
        # than silently taking Path(new_name).name: this is the one place
        # the category (aspect/behaviour) a rename keeps fixed could
        # otherwise look changeable to a caller who just typed a folder
        # prefix, matching how upload/_check_editable_file_name reject one too.
        if not new_basename or "/" in new_basename or "\\" in new_basename or new_basename in (".", ".."):
            raise ValueError(f"Invalid file name: '{new_name}' — expected a plain file name, not a path.")
        try:
            canonical_new_name = ArchiveLayout.canonicalize_name(new_basename)
        except ValueError as exc:
            raise ValueError(f"Invalid file name: '{new_basename}' — {exc}") from exc
        if Path(canonical_new_name).parent != Path(old_name).parent:
            raise ValueError(f"'{new_basename}' would change '{old_name}''s file type — rename within the same type instead.")
        new_name = canonical_new_name
        if new_name == old_name:
            raise ValueError("The new name is the same as the current one.")
        if new_name in existing_names:
            raise ValueError(f"'{new_name}' already exists.")

        archives = self._db.get_archives(project_id)
        archives[new_name] = archives.pop(old_name)

        updated_files: dict[str, bytes] = {}
        content_types: dict[str, str] = {}
        for reference_file in ("index.yml", "index.css"):
            content = archives.get(reference_file)
            if content is None:
                continue
            text = content.decode("utf-8")
            if old_basename not in text:
                continue
            new_content = text.replace(old_basename, new_basename).encode("utf-8")
            archives[reference_file] = new_content
            updated_files[reference_file] = new_content
            content_types[reference_file] = TEXT_CONTENT_TYPE_BY_EXTENSION[Path(reference_file).suffix.lower()]

        if "index.css" in updated_files:
            known_names = {Path(name).name for name in archives if name.startswith(f"{ASPECT_DIR}/")}
            missing = CssValidator.missing_references(archives["index.css"].decode("utf-8"), known_names)
            if missing:
                raise ValueError(f"index.css references missing file(s): {', '.join(sorted(missing))}.")

        try:
            _, family, _ = AutomatonBuilder.read_declared_env_keys(archives["index.yml"])
            new_automaton = AutomatonBuilder().build(archives, self._automaton_loader.known_projects_env_keys(project_id, family))
        except AutomatonBuildError:
            raise
        except Exception as exc:
            raise ValueError(f"Invalid project update: {exc}") from exc

        self._db.rename_project_file(Session().user, project_id, old_name, new_name, updated_files, content_types)
        project_id = await self._manager.finalize_update(project_id, new_automaton, commit)

        return {
            "success": True, "project_id": project_id, "old_name": old_name, "new_name": new_name,
            **self._file_undo_redo_info(project_id, new_name),
        }

    async def add_legal_terms(self, project_id: str, commit: CommitCallback) -> dict:
        """Seeds a fresh legal/terms.md with LEGAL_TERMS_SKELETON — the
        "New legal" file-explorer action. Rejects if the file already
        exists, since put_project_file would otherwise silently overwrite
        whatever the project owner already wrote there."""
        if project_id not in self._db.list_projects():
            raise FileNotFoundError(f"Project '{project_id}' does not exist.")
        if LEGAL_TERMS_FILE_NAME in self._db.list_archives(project_id):
            raise ValueError(f"'{LEGAL_TERMS_FILE_NAME}' already exists.")
        return await self.put_project_file(project_id, LEGAL_TERMS_FILE_NAME, LEGAL_TERMS_SKELETON, None, commit)

    async def _edit_index_yml(self, project_id: str, commit: CommitCallback, operation):
        """Runs `operation(editor: AutomatonYamlEditor) -> T` against
        `project_id`'s index.yml text, persists it via put_project_file,
        and returns `operation`'s own result untouched."""
        current = self._file_undo_redo_info(project_id, "index.yml")["content"]
        editor = AutomatonYamlEditor(current)
        result = operation(editor)
        await self.put_project_file(project_id, "index.yml", editor.serialize(), None, commit)
        return result

    async def add_state(self, project_id: str, commit: CommitCallback) -> StatePayload:
        return await self._edit_index_yml(project_id, commit, lambda editor: editor.add_state())

    async def add_signal(self, project_id: str, commit: CommitCallback) -> SignalPayload:
        return await self._edit_index_yml(project_id, commit, lambda editor: editor.add_signal())

    async def add_action(self, project_id: str, state_name: str, commit: CommitCallback) -> ActionPayload:
        return await self._edit_index_yml(project_id, commit, lambda editor: editor.add_action(state_name))

    async def set_state_field(
        self, project_id: str, state_name: str, field: str, value, commit: CommitCallback
    ) -> StatePayload:
        return await self._edit_index_yml(
            project_id, commit, lambda editor: editor.set_state_field(state_name, field, value)
        )

    async def set_action_field(
        self, project_id: str, state_name: str, action_name: str, field: str, value, commit: CommitCallback
    ) -> ActionPayload:
        return await self._edit_index_yml(
            project_id, commit, lambda editor: editor.set_action_field(state_name, action_name, field, value)
        )

    async def set_signal_field(
        self, project_id: str, signal_name: str, field: str, value, commit: CommitCallback
    ) -> SignalPayload:
        return await self._edit_index_yml(
            project_id, commit, lambda editor: editor.set_signal_field(signal_name, field, value)
        )

    async def set_init_action_field(self, project_id: str, field: str, value, commit: CommitCallback):
        return await self._edit_index_yml(
            project_id, commit, lambda editor: editor.set_init_action_field(field, value)
        )

    async def set_project_field(self, project_id: str, field: str, value, commit: CommitCallback) -> ProjectPayload:
        return await self._edit_index_yml(
            project_id, commit, lambda editor: editor.set_project_field(field, value)
        )

    async def delete_state(self, project_id: str, state_name: str, commit: CommitCallback) -> None:
        await self._edit_index_yml(project_id, commit, lambda editor: editor.delete_state(state_name))

    async def delete_action(self, project_id: str, state_name: str, action_name: str, commit: CommitCallback) -> None:
        await self._edit_index_yml(project_id, commit, lambda editor: editor.delete_action(state_name, action_name))

    async def delete_signal(self, project_id: str, signal_name: str, commit: CommitCallback) -> None:
        await self._edit_index_yml(project_id, commit, lambda editor: editor.delete_signal(signal_name))

    async def add_env_key(self, project_id: str, commit: CommitCallback) -> EnvKeyPayload:
        return await self._edit_index_yml(project_id, commit, lambda editor: editor.add_env_key())

    async def set_env_key_field(
        self, project_id: str, env_key_name: str, field: str, value, commit: CommitCallback
    ) -> EnvKeyPayload:
        return await self._edit_index_yml(
            project_id, commit, lambda editor: editor.set_env_key_field(env_key_name, field, value)
        )

    async def delete_env_key(self, project_id: str, env_key_name: str, commit: CommitCallback) -> None:
        await self._edit_index_yml(project_id, commit, lambda editor: editor.delete_env_key(env_key_name))

    @staticmethod
    def _source_archive(source_name: str) -> str:
        """Where a source's own backing content lives — one `<id>.csv`
        archive per source, under sources/, 1:1 and always in sync with
        its own id (kept that way by add_source/set_source_field/
        delete_source below), never user-named or picked from existing files."""
        return f"{SOURCES_DIR}/{source_name}.csv"

    async def add_source(
        self, project_id: str, commit: CommitCallback, name_hint: str | None = None, content: bytes = b"",
        driver: str = "avance",
    ) -> SourcePayload:
        """A source's own archive is created empty right alongside it —
        `url` is never left unconfigured (contrast env keys/signals, which
        start genuinely blank): AutomatonBuilder._build_source requires
        `url`'s own archive to already exist, so the archive write happens
        first, inside the same index.yml edit `operation`, before
        serialize()/put_project_file below revalidates the whole project
        against it. `driver`: 'avance' (default) — a fresh `sources/<id>.csv`
        archive, edited via SourceContentPanel.vue. 'env' — `url: avance:env`
        instead, no archive at all (an avance:env source has no file of its
        own — see tracking.sources.avance_env); the design view's own
        Source card shows its exported env keys read-only instead of the
        CSV editor. Declaring one with no exported env key yet is a real
        build error the very next save surfaces (AutomatonBuilder.
        _validate_env_sources) — same as any other not-yet-valid edit,
        never specially prevented here."""
        def operation(editor: AutomatonYamlEditor) -> SourcePayload:
            payload = editor.add_source(name_hint)
            if driver == "env":
                return editor.set_source_field(payload["name"], "url", "avance:env")
            archive_name = self._source_archive(payload["name"])
            self._db.save_project_file(Session().user, project_id, archive_name, content, "text/csv")
            return editor.set_source_field(payload["name"], "url", f"avance:{archive_name}")
        return await self._edit_index_yml(project_id, commit, operation)

    async def set_source_field(
        self, project_id: str, source_name: str, field: str, value, commit: CommitCallback
    ) -> SourcePayload:
        """A 'name' edit also renames the source's own archive (and the
        `url` field pointing at it) to match — same "editing this field
        renames the entry, and everything that follows it, together"
        contract set_signal_field's 'ui-label' case already has, just
        extended to a second, DB-backed side effect only sources have."""
        if field != "name":
            return await self._edit_index_yml(
                project_id, commit, lambda editor: editor.set_source_field(source_name, field, value)
            )

        def operation(editor: AutomatonYamlEditor) -> SourcePayload:
            payload = editor.set_source_field(source_name, field, value)
            new_name = payload["name"]
            if new_name == source_name:
                return payload
            old_archive = self._source_archive(source_name)
            new_archive = self._source_archive(new_name)
            if old_archive not in self._db.list_archives(project_id):
                return payload
            self._db.rename_project_file(Session().user, project_id, old_archive, new_archive)
            return editor.set_source_field(new_name, "url", f"avance:{new_archive}")
        return await self._edit_index_yml(project_id, commit, operation)

    async def delete_source(self, project_id: str, source_name: str, commit: CommitCallback) -> None:
        archive_name = self._source_archive(source_name)
        await self._edit_index_yml(project_id, commit, lambda editor: editor.delete_source(source_name))
        if archive_name in self._db.list_archives(project_id):
            self._db.delete_archive(project_id, archive_name)

    async def reorder_actions(
        self, project_id: str, state_name: str, action_name: str, position: int, commit: CommitCallback
    ) -> list[ActionPayload]:
        return await self._edit_index_yml(
            project_id, commit, lambda editor: editor.reorder_actions(state_name, action_name, position)
        )

    def _undo_redo_response(self, project_id: str, file_name: str, outcome: ContentRestored | FileRenamed, is_text: bool) -> dict:
        """Shared by undo_project_file/redo_project_file below: a plain
        content outcome reports on `file_name` itself; a rename outcome
        reports on the name the file actually moved to instead — the
        caller's own open file/tab must follow it, so the full {content,
        can_undo, can_redo, ...} is for that new name, not `file_name`."""
        if isinstance(outcome, FileRenamed):
            return {
                "success": True, "project_id": project_id, "renamed_to": outcome.active_name,
                **self._file_undo_redo_info(project_id, outcome.active_name),
            }
        user = Session().user
        return {
            "success": True,
            "project_id": project_id,
            "content": outcome.content.decode("utf-8") if is_text and outcome.content is not None else None,
            "can_undo": self._db.has_undo(user, project_id, file_name),
            "can_redo": self._db.has_redo(user, project_id, file_name),
        }

    async def undo_project_file(self, project_id: str, file_name: str, content: bytes) -> dict:
        """A pure editor preview, not a persisted change — never touches
        Archive or the automaton cache. `content` is the editor's current
        unsaved state, kept so a later redo can restore it (a rename step
        ignores it — see db/history.py's own undo_project_file)."""
        if project_id not in self._db.list_projects():
            raise FileNotFoundError(f"Project '{project_id}' does not exist.")
        existing_names = self._db.list_archives(project_id)
        resolved_name = self._resolve_file_name(project_id, file_name)
        if resolved_name not in existing_names:
            self._check_editable_file_name(file_name)
        file_name = resolved_name
        is_text = Path(file_name).suffix.lower() in TEXT_EDITABLE_EXTENSIONS
        raw_content = content.encode("utf-8") if is_text and isinstance(content, str) else content

        user = Session().user
        outcome = self._db.undo_project_file(user, project_id, file_name, raw_content)
        if outcome is None:
            raise ValueError(f"Nothing to undo for file '{file_name}'.")

        return self._undo_redo_response(project_id, file_name, outcome, is_text)

    async def redo_project_file(self, project_id: str, file_name: str, content: bytes) -> dict:
        """Mirror of undo_project_file, replaying the current user's own
        redo history instead (see db.Db.redo_project_file)."""
        if project_id not in self._db.list_projects():
            raise FileNotFoundError(f"Project '{project_id}' does not exist.")
        existing_names = self._db.list_archives(project_id)
        resolved_name = self._resolve_file_name(project_id, file_name)
        if resolved_name not in existing_names:
            self._check_editable_file_name(file_name)
        file_name = resolved_name
        is_text = Path(file_name).suffix.lower() in TEXT_EDITABLE_EXTENSIONS
        raw_content = content.encode("utf-8") if is_text and isinstance(content, str) else content

        user = Session().user
        outcome = self._db.redo_project_file(user, project_id, file_name, raw_content)
        if outcome is None:
            raise ValueError(f"Nothing to redo for file '{file_name}'.")

        return self._undo_redo_response(project_id, file_name, outcome, is_text)

    def clear_project_history(self, project_id: str) -> None:
        """Deletes the current user's undo/redo history for every file
        in `project_id`, so a fresh editing session starts clean."""
        if project_id not in self._db.list_projects():
            raise FileNotFoundError(f"Project '{project_id}' does not exist.")
        self._db.clear_history(Session().user, project_id)

    @staticmethod
    def _check_editable_file_name(file_name: str) -> None:
        if not file_name or file_name in (".", "..") or file_name.startswith("."):
            raise ValueError(f"Invalid file name: '{file_name}'.")
        try:
            canonical = ArchiveLayout.canonicalize_name(file_name)
        except ValueError as exc:
            raise ValueError(f"Invalid file name: '{file_name}' — {exc}") from exc
        if canonical != file_name:
            raise ValueError(f"Invalid file name: '{file_name}' — did you mean '{canonical}'?")

    async def delete_project_file(
        self, project_id: str, file_name: str, commit: CommitCallback
    ) -> None:
        """Deleting index.css cascades to every image asset it could have
        referenced — the file explorer's own "Theme" branch never offers
        deleting one of those individually while index.css still exists, so
        an orphaned asset would otherwise just be dead weight. Deleting an
        asset index.css still references is rejected outright instead:
        editing index.css's own text to drop the reference is exactly what
        the CSS editor is for, and rewriting it here on the asset's behalf
        risks mangling a rule irrecoverably for a small convenience."""

        if project_id not in self._db.list_projects():
            raise FileNotFoundError(f"Project '{project_id}' does not exist.")

        archives = self._db.get_archives(project_id=project_id)
        if file_name not in archives:
            matches = [name for name in archives if Path(name).name == file_name]
            if len(matches) == 1:
                file_name = matches[0]
        if file_name.startswith(f"{ASPECT_DIR}/"):
            index_css = archives.get("index.css")
            if index_css is not None and Path(file_name).name in CssValidator.referenced_basenames(index_css.decode("utf-8")):
                raise ValueError(
                    f"'{file_name}' is still referenced by index.css — remove the reference "
                    f"there first (or delete index.css itself, which takes its assets with it)."
                )

        try:
            del archives[file_name]
            cascade_names = (
                [name for name in archives if name.startswith(f"{ASPECT_DIR}/")]
                if file_name == "index.css" else []
            )
            for name in cascade_names:
                del archives[name]
            _, family, _ = AutomatonBuilder.read_declared_env_keys(archives["index.yml"])
            new_automaton = AutomatonBuilder().build(archives, self._automaton_loader.known_projects_env_keys(project_id, family))
        except AutomatonBuildError:
            raise
        except Exception as exc:
            raise ValueError(f"Invalid project definition: {exc}") from exc

        self._db.delete_archive(project_id, file_name)
        for name in cascade_names:
            self._db.delete_archive(project_id, name)
        await self._manager.finalize_update(project_id, new_automaton, commit)
