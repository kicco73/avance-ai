"""Db.rename_project_id: every FK-bearing table keyed on a project's own
`id` has to move together with it in the same transaction (see
db/projects.py's own docstring, and backend/src/docs/TECHNICAL_DEBT.md's
"PRAGMA foreign_keys" note) — Drive is one of them.
"""
from __future__ import annotations

import pytest

from db import Db

pytestmark = pytest.mark.contract

OLD_ID = "old_id"
NEW_ID = "new_id"


def test_renaming_a_project_carries_its_drive_files_to_the_new_id(db: Db):
    db.ensure_project(OLD_ID)
    db.write_drive_file(OLD_ID, "user", "reports/last.md", b"ciao", "text/markdown", None)

    db.rename_project_id(OLD_ID, NEW_ID)

    assert db.read_drive_file(NEW_ID, "user", "reports/last.md")[0] == b"ciao"
    assert db.read_drive_file(OLD_ID, "user", "reports/last.md") is None


def test_renaming_a_project_leaves_another_projects_drive_alone(db: Db):
    db.ensure_project(OLD_ID)
    db.ensure_project("untouched")
    db.write_drive_file("untouched", "user", "reports/last.md", b"altro", "text/markdown", None)

    db.rename_project_id(OLD_ID, NEW_ID)

    assert db.read_drive_file("untouched", "user", "reports/last.md")[0] == b"altro"
