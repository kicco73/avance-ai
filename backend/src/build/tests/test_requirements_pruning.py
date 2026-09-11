"""Pruning requirements.txt is the build's half of a skill owning its own
dependencies: the shared file keeps every line, and a copy that leaves a
skill out writes the file without the lines that skill claimed. The other
half — who may claim what — is core, and lives in
backend/tests/test_skill_owned_requirements.py.
"""
from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = pytest.mark.contract

BACKEND_DIR = Path(__file__).resolve().parent.parent.parent.parent
REQUIREMENTS = BACKEND_DIR / "requirements.txt"


def _lines(text: str) -> list[str]:
    return [line.strip() for line in text.splitlines() if line.strip()]


def test_a_build_without_a_skill_writes_a_requirements_file_without_its_line(tmp_path, monkeypatch):
    import build.backend_copy as backend_copy

    monkeypatch.setattr(backend_copy, "BUILDS_DIR", tmp_path / "builds")
    copy = backend_copy.BackendCopy(None, "p", 1, "p", ["talk"])
    monkeypatch.setattr(type(copy), "assembling", property(lambda self: tmp_path / "assembling"))
    (tmp_path / "assembling").mkdir()
    (tmp_path / "assembling" / "requirements.txt").write_text(REQUIREMENTS.read_text())

    copy._write_requirements()

    written = _lines((tmp_path / "assembling" / "requirements.txt").read_text())
    assert "piper-tts>=1.6" not in written
    assert "faster-whisper>=1.0" in written
    assert "fastapi>=0.115" in written


def test_a_build_that_leaves_nothing_out_writes_the_file_unchanged(tmp_path, monkeypatch):
    import build.backend_copy as backend_copy

    monkeypatch.setattr(backend_copy, "BUILDS_DIR", tmp_path / "builds")
    copy = backend_copy.BackendCopy(None, "p", 1, "p", [])
    monkeypatch.setattr(type(copy), "assembling", property(lambda self: tmp_path / "assembling"))
    (tmp_path / "assembling").mkdir()
    (tmp_path / "assembling" / "requirements.txt").write_text(REQUIREMENTS.read_text())

    copy._write_requirements()

    assert _lines((tmp_path / "assembling" / "requirements.txt").read_text()) == _lines(REQUIREMENTS.read_text())
