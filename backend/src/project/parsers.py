from __future__ import annotations

import io
import re
import zipfile
from pathlib import Path

import tinycss2

from automaton.automaton import Automaton
from automaton.automaton_builder import AutomatonBuilder
from db import Db

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


def extract_zip_safely(content: bytes, staging_dir: Path) -> None:
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
