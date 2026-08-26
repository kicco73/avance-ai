from __future__ import annotations

import io
import logging
import re
import zipfile
from pathlib import Path

import tinycss2

from automaton.automaton import Automaton
from automaton.automaton_builder import AutomatonBuilder
from db import Db

logger = logging.getLogger(__name__)

TEXT_EDITABLE_EXTENSIONS = {".yml", ".yaml", ".txt", ".md", ".csv", ".css"}

LEGAL_TERMS_FILE_NAME = "legal/terms.md"

TEXT_CONTENT_TYPE_BY_EXTENSION = {
    ".yml": "text/yaml",
    ".yaml": "text/yaml",
    ".txt": "text/plain",
    ".md": "text/markdown",
    ".csv": "text/csv",
    ".css": "text/css",
}

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

EDITABLE_EXTENSIONS = TEXT_EDITABLE_EXTENSIONS | IMAGE_EXTENSIONS

MAX_IMAGE_UPLOAD_BYTES = 5 * 1024 * 1024

SESSIONS_EXPORT_FILENAME = "sessions.json"
BENCHMARK_EXPORT_FILENAME = "benchmark.json"
BUNDLE_FILE_NAMES = {SESSIONS_EXPORT_FILENAME, BENCHMARK_EXPORT_FILENAME}

ASPECT_DIR = "aspect"
BEHAVIOUR_DIR = "behaviour"
ROOT_FILE_NAMES = {"index.yml", "index.css"}
ASPECT_EXTENSIONS = IMAGE_EXTENSIONS | {".css"}
BEHAVIOUR_EXTENSIONS = {".txt", ".md", ".csv"}


def canonical_archive_name(name: str) -> str:
    basename = Path(name).name
    if basename in ROOT_FILE_NAMES:
        if name != basename:
            raise ValueError(f"'{basename}' must be at the project root, not '{name}'.")
        return basename
    if name == LEGAL_TERMS_FILE_NAME:
        return name
    extension = Path(basename).suffix.lower()
    if extension in ASPECT_EXTENSIONS:
        return f"{ASPECT_DIR}/{basename}"
    if extension in BEHAVIOUR_EXTENSIONS:
        return f"{BEHAVIOUR_DIR}/{basename}"
    raise ValueError(f"Unsupported file extension for '{name}': '{extension or '(none)'}'.")


def decode_text_archives(archives: dict[str, bytes]) -> dict[str, str | bytes]:
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
    names = set()
    for _, target in _CSS_URL_PATTERN.findall(css_text):
        target = target.strip()
        if not target or _ABSOLUTE_URL_PATTERN.match(target):
            continue
        names.add(Path(target).name)
    return names


def missing_css_references(css_text: str, known_archive_names: set[str]) -> list[str]:
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
        elif node_type in ("() block", "[] block", "{} block") and node.content is not None:
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


def looks_like_zip(content_type: str | None, content: bytes) -> bool:
    if content_type:
        media_type = content_type.split(";")[0].strip().lower()
        if "zip" in media_type:
            return True
        if "yaml" in media_type or "yml" in media_type:
            return False
    return content[:4] == b"PK\x03\x04"


_RESERVED_ZIP_DIRS = {ASPECT_DIR, BEHAVIOUR_DIR, "legal"}


def extract_zip_safely(content: bytes, staging_dir: Path) -> None:
    """Validates zip-slip safety and that 'index.yml' sits at the zip's
    own root — a single top-level wrapper folder (e.g. a GitHub download
    or drag-a-folder zip) is stripped first, so it counts as root too.
    Every other file is canonicalized into this project's own layout
    (root, 'aspect/', 'behaviour/', 'legal/' — see canonical_archive_name)
    by extension, regardless of where it originally sat in the zip."""
    with zipfile.ZipFile(io.BytesIO(content)) as zf:
        names = [entry.replace("\\", "/") for entry in zf.namelist()]
        # macOS's Finder/Archive Utility tacks on a __MACOSX/ sidecar
        # folder full of resource-fork metadata (AppleDouble ._filename
        # entries, nothing of actual interest) whenever it zips a folder.
        names = [n for n in names if n.split("/", 1)[0] != "__MACOSX"]

        for name in names:
            if name.startswith("/") or any(part == ".." for part in Path(name).parts):
                raise ValueError(f"Unsafe path inside zip: '{name}'.")

        file_names = [n for n in names if not n.endswith("/")]
        flat_names = [n for n in file_names if "/" not in n]
        top_level_dirs = {n.split("/", 1)[0] for n in file_names if "/" in n}
        wrapper_dirs = top_level_dirs - _RESERVED_ZIP_DIRS

        if wrapper_dirs and (flat_names or (top_level_dirs - wrapper_dirs) or len(wrapper_dirs) > 1):
            raise ValueError(
                "Zip must be either the project's own layout (index.yml/index.css at the root, "
                "assets under 'aspect/', 'behaviour/', 'legal/') or a single folder wrapping that "
                "same layout."
            )
        prefix = f"{next(iter(wrapper_dirs))}/" if wrapper_dirs else ""
        effective = {n: n[len(prefix):] for n in file_names}

        if "index.yml" not in effective.values():
            raise ValueError("Zip must contain an 'index.yml' file at its root.")

        for original, stripped in effective.items():
            if stripped in BUNDLE_FILE_NAMES:
                continue
            top = stripped.split("/", 1)[0]
            nested_ok = (
                "/" not in stripped
                or stripped == LEGAL_TERMS_FILE_NAME
                or (top in (ASPECT_DIR, BEHAVIOUR_DIR) and stripped.count("/") == 1)
            )
            if not nested_ok:
                raise ValueError(f"Unsupported path inside zip: '{original}'.")

        canonical: dict[str, str] = {}
        for original, stripped in effective.items():
            canonical[original] = stripped if stripped in BUNDLE_FILE_NAMES else canonical_archive_name(stripped)

        by_canonical: dict[str, str] = {}
        for original, name in canonical.items():
            clash = by_canonical.get(name)
            if clash is not None:
                raise ValueError(f"'{original}' and '{clash}' both resolve to the same file '{name}'.")
            by_canonical[name] = original

        for original, name in canonical.items():
            target = staging_dir / name
            target.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(original) as src, open(target, "wb") as dst:
                dst.write(src.read())


class AutomatonLoader:
    def __init__(self, db: Db) -> None:
        self._db = db
        # (project_name, revision) -> Automaton. Revision-keyed so a caller
        # pinned to one specific revision and a caller wanting "whatever's
        # current" can share the cache without cross-serving.
        self._automaton_cache: dict[tuple[str, int], Automaton] = {}

    @staticmethod
    def is_safe_project_name(project_name: str) -> bool:
        """No path traversal: must be a single plain path segment — not
        empty, not '.'/'..', no separators, resolving to itself when
        treated as a bare filename."""
        if not project_name or project_name in (".", ".."):
            return False
        return Path(project_name).name == project_name

    def known_projects_env_keys(self, project_name: str) -> dict[str, frozenset[str]]:
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

    def invalidate_cache(self, project_name: str) -> None:
        """Drops every cached revision of `project_name`, for callers that
        can't tell which revisions are now stale. Ordinary edits go through
        ProjectManager.finalize_update instead, which re-caches just one revision."""
        for key in [k for k in self._automaton_cache if k[0] == project_name]:
            del self._automaton_cache[key]

    def set_cached(self, project_name: str, revision: int, automaton: Automaton) -> None:
        self._automaton_cache[(project_name, revision)] = automaton

    def load_at_revision(self, project_name: str, revision: int) -> Automaton:
        cache_key = (project_name, revision)
        cached = self._automaton_cache.get(cache_key)
        if cached is not None:
            return cached

        if not AutomatonLoader.is_safe_project_name(project_name):
            raise ValueError(f"Invalid project name: '{project_name}'.")

        archives = self._db.get_archives(project_name, revision=revision)

        if not archives:
            raise  FileNotFoundError(f"Project '{project_name}' does not exist.")
        if 'index.yml' not in archives:
            raise  FileNotFoundError(f"Project '{project_name}' does not contain 'index.yml'.")

        automaton = AutomatonBuilder().build(
            decode_text_archives(archives), self.known_projects_env_keys(project_name)
        )
        self._automaton_cache[cache_key] = automaton
        return automaton

    def load(self, project_name: str) -> Automaton:
        """Whatever's current for `project_name` right now — the most
        recent draft, published or not. A caller needing a specific,
        possibly older revision uses load_at_revision directly."""
        revision = self._db.get_project_revision(project_name)
        return self.load_at_revision(project_name, revision)
