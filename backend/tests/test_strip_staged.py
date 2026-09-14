from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.contract

BIN = Path(__file__).resolve().parents[1] / "bin"

sys.path.insert(0, str(BIN))
_spec = importlib.util.spec_from_file_location("strip_staged", BIN / "strip_staged.py")
strip_staged = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(strip_staged)


@pytest.fixture
def repository(tmp_path, monkeypatch):
    def git(*arguments):
        return subprocess.run(("git",) + arguments, cwd=tmp_path, capture_output=True, text=True, check=True).stdout

    git("init", "-q")
    git("config", "user.email", "t@t")
    git("config", "user.name", "t")
    monkeypatch.chdir(tmp_path)
    return git


def stage(repository, tmp_path, name, text):
    (tmp_path / name).write_text(text, encoding="utf-8")
    repository("add", "--", name)


def test_the_staged_content_loses_its_comments_and_so_does_the_working_copy(repository, tmp_path):
    stage(repository, tmp_path, "a.py", "x = 1  # gone\n")
    assert strip_staged.main() == 0
    assert repository("show", ":a.py") == "x = 1\n"
    assert (tmp_path / "a.py").read_text(encoding="utf-8") == "x = 1\n"


def test_an_unstaged_edit_by_someone_else_is_never_swept_into_the_commit(repository, tmp_path):
    stage(repository, tmp_path, "a.py", "x = 1  # gone\n")
    (tmp_path / "a.py").write_text("x = 1  # gone\ntheirs = 2\n", encoding="utf-8")
    assert strip_staged.main() == 0
    assert repository("show", ":a.py") == "x = 1\n"
    assert (tmp_path / "a.py").read_text(encoding="utf-8") == "x = 1  # gone\ntheirs = 2\n"


def test_a_file_that_cannot_be_parsed_is_left_alone(repository, tmp_path, capsys):
    stage(repository, tmp_path, "broken.py", "def f(:\n")
    assert strip_staged.main() == 0
    assert repository("show", ":broken.py") == "def f(:\n"
    assert "could not be parsed" in capsys.readouterr().err


def test_a_stylesheet_keeps_its_comments(repository, tmp_path):
    stage(repository, tmp_path, "a.css", ".a { color: red }\n/* kept */\n")
    assert strip_staged.main() == 0
    assert repository("show", ":a.css") == ".a { color: red }\n/* kept */\n"
