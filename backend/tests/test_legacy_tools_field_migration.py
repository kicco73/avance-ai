"""project.archive.legacy_tools_field_migration — the boot-time, one-off
rewrite of every stored index.yml revision whose state(s) still declare
the now-removed `tools:` field into `ai-may-query-sources:` (see
automaton.State.ai_may_query_sources), same overall shape as
legacy_source_read_migration.py's own migration off source.<name>.read().
"""
from __future__ import annotations

import pytest

from project.archive.legacy_tools_field_migration import migrate_legacy_tools_field

pytestmark = pytest.mark.contract

PROJECT_ID = "proj"

INDEX_YML = """\
project:
  id: {project_id}
sources:
  flights:
    url: avance:flights.csv
    ai-definition: One row per flight.
init-action:
  target: a
states:
  a:
    ui-label: State A
    contextual-prompt: hi
    tools: [flights]
    actions:
      - name: go-b
        ui-label: Go to B
        target: b
  b:
    ui-label: State B
    contextual-prompt: there
"""

# Same shape, but the referenced source has no ai-definition yet — the
# rewritten YAML fails to build (ai-definition is required for a source
# listed as a tool), so this one must be left untouched.
INDEX_YML_NO_AI_DEFINITION = """\
project:
  id: {project_id}
sources:
  flights:
    url: avance:flights.csv
init-action:
  target: a
states:
  a:
    ui-label: State A
    contextual-prompt: hi
    tools: [flights]
    actions:
      - name: go-b
        ui-label: Go to B
        target: b
  b:
    ui-label: State B
    contextual-prompt: there
"""


def _seed(db, project_id: str, revision: int, index_yml: str) -> None:
    db.ensure_project(project_id)
    db.write_archive_at_revision(project_id, "index.yml", revision, index_yml.encode("utf-8"), "text/plain")
    db.write_archive_at_revision(project_id, "flights.csv", revision, b"a,b\n1,2\n", "text/csv")


def test_rewrites_a_stored_revision_off_the_tools_field(db):
    _seed(db, PROJECT_ID, 0, INDEX_YML.format(project_id=PROJECT_ID))

    migrate_legacy_tools_field(db)

    rewritten = db.get_archive(PROJECT_ID, "index.yml", revision=0).decode("utf-8")
    assert "ai-may-query-sources:" in rewritten
    assert "tools:" not in rewritten


def test_a_revision_with_no_tools_field_is_left_byte_identical(db):
    content = INDEX_YML.format(project_id=PROJECT_ID).replace("    tools: [flights]\n", "")
    _seed(db, PROJECT_ID, 0, content)

    migrate_legacy_tools_field(db)

    assert db.get_archive(PROJECT_ID, "index.yml", revision=0).decode("utf-8") == content


def test_migrates_every_stored_revision_independently(db):
    _seed(db, PROJECT_ID, 0, INDEX_YML.format(project_id=PROJECT_ID))
    _seed(db, PROJECT_ID, 1, INDEX_YML.format(project_id=PROJECT_ID))

    migrate_legacy_tools_field(db)

    for revision in (0, 1):
        rewritten = db.get_archive(PROJECT_ID, "index.yml", revision=revision).decode("utf-8")
        assert "ai-may-query-sources:" in rewritten


def test_a_source_with_no_ai_definition_yet_is_left_untouched(db):
    content = INDEX_YML_NO_AI_DEFINITION.format(project_id=PROJECT_ID)
    _seed(db, PROJECT_ID, 0, content)

    migrate_legacy_tools_field(db)  # must not raise

    assert db.get_archive(PROJECT_ID, "index.yml", revision=0).decode("utf-8") == content


def test_one_broken_revision_never_blocks_another_projects_migration(db):
    # Contains the pattern the candidate scan looks for, but isn't valid
    # YAML — must be caught and logged, never crash the whole boot.
    _seed(db, "broken", 0, "tools: [x] — this is not valid yaml: [")
    _seed(db, "good", 0, INDEX_YML.format(project_id="good"))

    migrate_legacy_tools_field(db)  # must not raise

    rewritten = db.get_archive("good", "index.yml", revision=0).decode("utf-8")
    assert "ai-may-query-sources:" in rewritten


def test_no_candidates_means_no_backup_is_taken(db, monkeypatch):
    content = INDEX_YML.format(project_id=PROJECT_ID).replace("    tools: [flights]\n", "")
    _seed(db, PROJECT_ID, 0, content)
    calls = []
    monkeypatch.setattr(db, "backup_now", lambda reason: calls.append(reason))

    migrate_legacy_tools_field(db)

    assert calls == []


def test_a_backup_is_taken_before_migrating_when_there_is_something_to_migrate(db, monkeypatch):
    _seed(db, PROJECT_ID, 0, INDEX_YML.format(project_id=PROJECT_ID))
    calls = []
    monkeypatch.setattr(db, "backup_now", lambda reason: calls.append(reason))

    migrate_legacy_tools_field(db)

    assert len(calls) == 1
