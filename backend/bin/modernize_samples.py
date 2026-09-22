"""Rewrites every sample project's index.yml — unpacked folders and zips
alike — with the same modernizer a build applies to an uploaded or
stored one, so what is checked in is spelled the way the format reads
it today. A sample the modernizer cannot fully settle is rewritten as
far as it goes and named, for a person to finish.

    cd backend && python3 bin/modernize_samples.py
"""
from __future__ import annotations

import shutil
import sys
import tempfile
import zipfile
from pathlib import Path

sys.path.insert(0, "src")

from automaton.automaton_builder import AutomatonBuilder  # noqa: E402
from automaton.build_error import AutomatonBuildError  # noqa: E402
from automaton.index_yml_modernizer import IndexYmlModernizer  # noqa: E402
from build.compiler import read_project_contents  # noqa: E402

ROOTS = (Path("samples/projects"), Path("src/avance_platform"))


def _modernized(text: str) -> tuple[str, tuple[str, ...]]:
    outcome = IndexYmlModernizer().modernize(text)
    return outcome.text, outcome.fixes


def _folder(project: Path) -> tuple[str, ...]:
    index = project / "index.yml"
    text, fixes = _modernized(index.read_text(encoding="utf-8"))
    index.write_text(text, encoding="utf-8")
    return fixes


def _zip(archive: Path) -> tuple[str, ...]:
    with zipfile.ZipFile(archive) as source:
        names = [name for name in source.namelist() if Path(name).name == "index.yml" and "__MACOSX" not in name]
        if not names:
            return ()
        text, fixes = _modernized(source.read(names[0]).decode("utf-8"))
        if not fixes:
            return ()
        rewritten = Path(tempfile.mkdtemp()) / archive.name
        with zipfile.ZipFile(rewritten, "w", zipfile.ZIP_DEFLATED) as target:
            for info in source.infolist():
                data = text.encode("utf-8") if info.filename == names[0] else source.read(info)
                target.writestr(info, data)
    shutil.move(str(rewritten), archive)
    return fixes


def _builds(project: Path) -> str:
    try:
        AutomatonBuilder().build(read_project_contents(project))
        return "builds"
    except AutomatonBuildError as refusal:
        return f"still refused: {refusal}"


def main() -> None:
    for root in ROOTS:
        for entry in sorted(root.iterdir()):
            is_folder = entry.is_dir() and (entry / "index.yml").exists()
            is_zip = entry.suffix == ".zip"
            for _ in filter(None, [is_folder or is_zip]):
                fixes = _folder(entry) if is_folder else _zip(entry)
                print(f"{entry}: {len(fixes)} fix(es), {_builds(entry)}")


if __name__ == "__main__":
    main()
