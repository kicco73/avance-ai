#!/usr/bin/env python3
import re
import subprocess
import sys
from pathlib import Path

MAIN = Path(__file__).resolve().parents[1] / "src" / "main.py"

ASSIGNMENT = re.compile(r'^(__version__ = ")(\d+)\.(\d+)\.(\d+)(")', re.MULTILINE)


def bumped(source):
    match = ASSIGNMENT.search(source)
    if match is None:
        raise ValueError('no __version__ = "x.y.z" line')
    revision = int(match.group(4)) + 1
    line = f"{match.group(1)}{match.group(2)}.{match.group(3)}.{revision}{match.group(5)}"
    return source[: match.start()] + line + source[match.end():], f"{match.group(2)}.{match.group(3)}.{revision}"


def main():
    source = MAIN.read_text(encoding="utf-8")
    try:
        text, version = bumped(source)
    except ValueError as error:
        print(f"bump_version: {MAIN}: {error}", file=sys.stderr)
        return 1
    MAIN.write_text(text, encoding="utf-8")
    subprocess.run(("git", "add", "--", str(MAIN)), check=True)
    print(f"bump_version: {version}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
