from __future__ import annotations

from pathlib import Path

from automaton.file_types import ASPECT_DIR, BEHAVIOUR_DIR, ROOT_FILE_NAMES, ProjectFileTypes  # noqa: F401  (re-exported: the project package reads the layout's own names from here)

# -- Project file layout -------------------------------------------------
# Shared schema for how a project's files are named and where each one
# lives on disk. What an extension itself means — content type, folder,
# upload limit — is automaton.file_types' single catalog, imported above.

LEGAL_TERMS_FILE_NAME = "legal/terms.md"

# Seeded by ProjectEditor.add_legal_terms into a fresh legal/terms.md —
# per-app terms shown once, on top of the platform's own general Terms of
# Service (see backend/src/docs/TERMS.md).
LEGAL_TERMS_SKELETON = """# Terms of this application

These are the specific terms of this application, in addition to the
platform's general Terms of Use and Privacy Policy.

## What this application does with your data

[Describe here what data this application collects and what it is used for.]

## Permissions requested

[Describe here the specific permissions this application needs, if any.]

## Retention

[State here how long this application's data is retained.]
"""

SESSIONS_EXPORT_FILENAME = "sessions.json"
TESTS_EXPORT_FILENAME = "tests.json"
BUNDLE_FILE_NAMES = {SESSIONS_EXPORT_FILENAME, TESTS_EXPORT_FILENAME}

# One `<id>.csv` archive per `sources:` entry of the "avance" driver — its
# own backing store (tracking.sources.avance_archive), created empty
# alongside the source (ProjectEditor.add_source) and renamed/deleted in
# lockstep with it (set_source_field/delete_source).
SOURCES_DIR = "sources"
# A purely runtime, per-chat-session scratch namespace — never part of a
# project's own versioned definition (ProjectManager.export_project_zip
# omits every archive under here entirely), never user-facing, never
# reached through ArchiveLayout.canonicalize_name below. Today the only
# thing living under it is AvanceArchiveSource's own per-session read
# cache, at `{CACHE_DIR}/sessions/<chat session id>/{SOURCES_DIR}/<id>.csv`
# (Db.write_archive_at_revision writes it, Db.delete_archives_with_prefix
# — called from SessionManager.close_session/TurnService.
# delete_session — cleans up everything under a closed/deleted session's
# own subtree).
CACHE_DIR = "cache"


class ArchiveLayout:
    """Where a project's files live and how their bytes are represented —
    canonicalizing an uploaded/imported name into this project's own
    layout (root, aspect/, behaviour/, legal/), and decoding text archives
    for parsing."""

    @staticmethod
    def canonicalize_name(name: str) -> str:
        basename = Path(name).name
        if basename in ROOT_FILE_NAMES:
            if name != basename:
                raise ValueError(f"'{basename}' must be at the project root, not '{name}'.")
            return basename
        if name == LEGAL_TERMS_FILE_NAME:
            return name
        # sources/<id>.csv (a source's own backing archive — see
        # SOURCES_DIR's own docstring) is already canonical, exactly as
        # given: without this, its ".csv" extension would otherwise fall
        # through to the generic behaviour-folder rule below and get
        # silently rerouted to behaviour/<id>.csv — wrong archive
        # entirely, and exactly what ProjectEditor.put_project_file's own
        # "does this already-known archive need canonicalizing at all?"
        # fallback would do the moment a source's own archive isn't
        # already in Db (e.g. one predating this driver's own auto-provisioning).
        parts = Path(name).parts
        if len(parts) == 2 and parts[0] == SOURCES_DIR and Path(basename).suffix.lower() == ".csv":
            return name
        return ProjectFileTypes.of(basename).canonical_name(basename)

    @staticmethod
    def decode_text(archives: dict[str, bytes]) -> dict[str, str | bytes]:
        decoded: dict[str, str | bytes] = {}
        for name, content in archives.items():
            if ProjectFileTypes.of(name).text and isinstance(content, (bytes, bytearray)):
                decoded[name] = content.decode("utf-8")
            else:
                decoded[name] = content
        return decoded
