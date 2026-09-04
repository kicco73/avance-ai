from __future__ import annotations

import io
import zipfile
from pathlib import Path

from .layout import ASPECT_DIR, BEHAVIOUR_DIR, BUNDLE_FILE_NAMES, CACHE_DIR, LEGAL_TERMS_FILE_NAME, ArchiveLayout


class ZipImporter:
    """Recognizes and safely unpacks a project zip upload/import into a
    staging directory, in this project's own layout."""

    _RESERVED_DIRS = {ASPECT_DIR, BEHAVIOUR_DIR, "legal", CACHE_DIR}

    @staticmethod
    def looks_like_zip(content_type: str | None, content: bytes) -> bool:
        if content_type:
            media_type = content_type.split(";")[0].strip().lower()
            if "zip" in media_type:
                return True
            if "yaml" in media_type or "yml" in media_type:
                return False
        return content[:4] == b"PK\x03\x04"

    @classmethod
    def extract_safely(cls, content: bytes, staging_dir: Path) -> None:
        """Validates zip-slip safety and that 'index.yml' sits at the zip's
        own root — a single top-level wrapper folder (e.g. a GitHub download
        or drag-a-folder zip) is stripped first, so it counts as root too.
        Every other file is canonicalized into this project's own layout
        (root, 'aspect/', 'behaviour/', 'legal/' — see
        ArchiveLayout.canonicalize_name) by extension, regardless of where
        it originally sat in the zip."""
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
            wrapper_dirs = top_level_dirs - cls._RESERVED_DIRS

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
                    or (top in (ASPECT_DIR, BEHAVIOUR_DIR, CACHE_DIR) and stripped.count("/") == 1)
                )
                if not nested_ok:
                    raise ValueError(f"Unsupported path inside zip: '{original}'.")

            # cache/<id>.csv bypasses canonicalize_name entirely: it's not
            # a file a user ever names/uploads through the editable-file
            # path canonicalize_name serves (see layout.py's own CACHE_DIR
            # docstring) — its ".csv" extension would otherwise get routed
            # under behaviour/ like any other user-uploaded .csv, silently
            # detaching it from the source that keeps its own url in sync
            # with this exact path.
            canonical: dict[str, str] = {}
            for original, stripped in effective.items():
                if stripped in BUNDLE_FILE_NAMES or stripped.split("/", 1)[0] == CACHE_DIR:
                    canonical[original] = stripped
                else:
                    canonical[original] = ArchiveLayout.canonicalize_name(stripped)

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
