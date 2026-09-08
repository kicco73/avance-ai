#!/usr/bin/env python3
"""Compiles a project directory or zip into a package, from the command
line. The compiler itself lives in src/build/compiler.py, which the Build
view calls too — this file is only the CLI around it, kept because
compiling a project sitting on disk (a sample, a zip someone sent) has no
equivalent in the panel, which only ever sees stored projects.

Usage:
    python compile_automaton.py <project_dir_or_zip> --automaton-module <name> [--backend-src DIR]
    python compile_automaton.py <project_dir_or_zip> --automaton-module <name> --verify
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

_BACKEND_SRC = Path(__file__).resolve().parent.parent / "src"
if str(_BACKEND_SRC) not in sys.path:
    sys.path.insert(0, str(_BACKEND_SRC))

from build.compiler import CompileError, compile_package, verify_package  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("project", type=Path, help="Project directory or zip file")
    parser.add_argument(
        "--automaton-module", required=True, dest="module_name",
        help="Name of the generated package (written under --backend-src)",
    )
    parser.add_argument(
        "--backend-src", type=Path, default=_BACKEND_SRC,
        help="Directory to write the generated package into (default: this repo's own backend/src/)",
    )
    parser.add_argument("--verify", action="store_true", help="Check an already-compiled package against the project")
    args = parser.parse_args()

    try:
        if args.verify:
            verify_package(args.project, args.module_name)
        else:
            print(f"Wrote {compile_package(args.project, args.module_name, args.backend_src)}")
    except CompileError as exc:
        print(f"error: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
