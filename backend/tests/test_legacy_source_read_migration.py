"""project.archive.legacy_source_read_migration — the boot-time, one-off
rewrite of every stored index.yml revision still calling the removed
source.<name>.read() into attachment.read(name) wherever it safely can.
"""
from __future__ import annotations

import pytest

from project.archive.legacy_source_read_migration import migrate_legacy_source_read

pytestmark = pytest.mark.contract

PROJECT_ID = "proj"

INDEX_YML = """\
project:
  id: {project_id}
init-action:
  target: a
sources:
  pino:
    url: avance:flights.csv
states:
  a:
    ui-label: State A
    contextual-prompt: hi
    actions:
      - name: go-b
        ui-label: Go to B
        target: b
        on-enter: |
          actuator.notify('Hi', source.pino.read())
  b:
    ui-label: State B
    contextual-prompt: there
"""


def _seed(db, project_id: str, revision: int, index_yml: str) -> None:
    db.ensure_project(project_id)
    db.write_archive_at_revision(project_id, "index.yml", revision, index_yml.encode("utf-8"), "text/plain")
    db.write_archive_at_revision(project_id, "flights.csv", revision, b"a,b\n1,2\n", "text/csv")


def test_rewrites_a_stored_revision_off_source_read(db):
    _seed(db, PROJECT_ID, 0, INDEX_YML.format(project_id=PROJECT_ID))

    migrate_legacy_source_read(db)

    rewritten = db.get_archive(PROJECT_ID, "index.yml", revision=0).decode("utf-8")
    assert "attachment.read('flights.csv')" in rewritten
    assert "source.pino.read()" not in rewritten


def test_a_revision_with_no_source_read_is_left_byte_identical(db):
    content = INDEX_YML.format(project_id=PROJECT_ID).replace("source.pino.read()", "'nothing to migrate'")
    _seed(db, PROJECT_ID, 0, content)

    migrate_legacy_source_read(db)

    assert db.get_archive(PROJECT_ID, "index.yml", revision=0).decode("utf-8") == content


def test_migrates_every_stored_revision_independently(db):
    _seed(db, PROJECT_ID, 0, INDEX_YML.format(project_id=PROJECT_ID))
    _seed(db, PROJECT_ID, 1, INDEX_YML.format(project_id=PROJECT_ID))

    migrate_legacy_source_read(db)

    for revision in (0, 1):
        rewritten = db.get_archive(PROJECT_ID, "index.yml", revision=revision).decode("utf-8")
        assert "attachment.read('flights.csv')" in rewritten


def test_an_unresolvable_read_call_is_left_untouched(db):
    content = INDEX_YML.format(project_id=PROJECT_ID).replace("source.pino.read()", "source.nope.read()")
    _seed(db, PROJECT_ID, 0, content)

    migrate_legacy_source_read(db)

    assert db.get_archive(PROJECT_ID, "index.yml", revision=0).decode("utf-8") == content


def test_one_broken_revision_never_blocks_another_projects_migration(db):
    # Contains the pattern the candidate scan looks for, but isn't valid
    # YAML — must be caught and logged, never crash the whole boot.
    _seed(db, "broken", 0, "source.x.read() — this is not valid yaml: [")
    _seed(db, "good", 0, INDEX_YML.format(project_id="good"))

    migrate_legacy_source_read(db)  # must not raise

    rewritten = db.get_archive("good", "index.yml", revision=0).decode("utf-8")
    assert "attachment.read('flights.csv')" in rewritten


def test_no_candidates_means_no_backup_is_taken(db, monkeypatch):
    content = INDEX_YML.format(project_id=PROJECT_ID).replace("source.pino.read()", "'clean'")
    _seed(db, PROJECT_ID, 0, content)
    calls = []
    monkeypatch.setattr(db, "backup_now", lambda reason: calls.append(reason))

    migrate_legacy_source_read(db)

    assert calls == []


def test_a_backup_is_taken_before_migrating_when_there_is_something_to_migrate(db, monkeypatch):
    _seed(db, PROJECT_ID, 0, INDEX_YML.format(project_id=PROJECT_ID))
    calls = []
    monkeypatch.setattr(db, "backup_now", lambda reason: calls.append(reason))

    migrate_legacy_source_read(db)

    assert len(calls) == 1
