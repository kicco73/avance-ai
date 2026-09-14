#!/usr/bin/env python3
import subprocess
import sys
import tokenize
from pathlib import Path

import strip_comments

WRITABLE = ("100644", "100755")


def git(*arguments, stdin=None):
    return subprocess.run(("git",) + arguments, input=stdin, capture_output=True, text=True, check=True).stdout


def staged_paths():
    return [path for path in git("diff", "--cached", "--name-only", "--diff-filter=ACM", "-z").split("\0") if path]


def strip(path, root, stripper):
    mode = git("ls-files", "-s", "--", path).split(" ", 1)[0]
    if mode not in WRITABLE:
        return
    text = git("show", f":{path}")
    if strip_comments.SENTINEL in text:
        return
    try:
        clean = stripper.stripped(path, text)
    except (tokenize.TokenError, IndentationError, SyntaxError) as error:
        print(f"strip_staged: left alone, could not be parsed: {path}: {error}", file=sys.stderr)
        return
    if clean == text:
        return
    sha = git("hash-object", "-w", "--path", path, "--stdin", stdin=clean).strip()
    git("update-index", "--cacheinfo", f"{mode},{sha},{path}")
    working_copy = root / path
    if working_copy.read_text(encoding="utf-8") != text:
        print(f"strip_staged: stripped {path}, staged only: the working copy differed and was left alone")
        return
    working_copy.write_text(clean, encoding="utf-8")
    print(f"strip_staged: stripped {path}")


def main():
    try:
        root = Path(git("rev-parse", "--show-toplevel").strip())
        stripper = strip_comments.Stripper()
        for path in staged_paths():
            if stripper.handles(path):
                strip(path, root, stripper)
    except (OSError, subprocess.CalledProcessError) as error:
        print(f"strip_staged: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
