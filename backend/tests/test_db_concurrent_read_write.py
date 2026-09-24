from __future__ import annotations

import sqlite3

import pytest

from db.db import Db

pytestmark = pytest.mark.contract


def test_a_write_commits_while_another_connection_is_in_the_middle_of_a_read(tmp_path):
    path = tmp_path / "working.db"
    db = Db(f"sqlite:///{path}")
    db.ensure_project("before")
    reader = sqlite3.connect(path, timeout=0)
    reader.execute("BEGIN")
    reader.execute("SELECT id FROM Project").fetchall()

    try:
        db.ensure_project("during")
        assert [row[0] for row in reader.execute("SELECT id FROM Project")] == ["before"]
    finally:
        reader.rollback()
        reader.close()

    assert db.project_exists("during")
