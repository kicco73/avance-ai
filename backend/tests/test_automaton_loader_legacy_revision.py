"""A revision already stored in the Archive table must stay loadable
forever — ChatSession.project_revision pins every native/imported
session to the revision published when it was created, and every
endpoint touching such a session (state, messages, timeline, tests…)
builds that exact revision. When AutomatonBuilder started requiring
`project.id`, revisions stored before that (no `project:` section at
all) stopped building, and every session pinned to one answered 500
"project.id None is required…" — on whichever endpoint happened to
touch it. The loader now supplies the Archive row's own project_id as
the legacy default; an *upload* without project.id is still rejected.
"""
from __future__ import annotations

import pytest

from automaton.automaton_builder import AutomatonBuilder
from project.archive.automaton_loader import AutomatonLoader

pytestmark = pytest.mark.regression

PROJECT_ID = "legacy_proj"

# What an index.yml looked like before `project:` existed at all.
PRE_PROJECT_SECTION_YML = """
avance-version: "1.7.0"
init-action:
  target: a
states:
  a:
    ui-label: A
    contextual-prompt: hi
"""

# A `project:` section that predates the `id` field.
PRE_PROJECT_ID_YML = """
project:
  ui-label: Old label
init-action:
  target: a
states:
  a:
    ui-label: A
    contextual-prompt: hi
"""

CURRENT_YML = """
project:
  id: legacy_proj
  revision: 1
init-action:
  target: b
states:
  b:
    ui-label: B
    contextual-prompt: hi
"""


def _store(db, yml: str) -> int:
    db.ensure_project(PROJECT_ID)
    db.save_project_files(PROJECT_ID, {"index.yml": yml.encode("utf-8")}, {"index.yml": "text/yaml"})
    db.publish_project(PROJECT_ID)
    return db.get_project_published_revision(PROJECT_ID)


@pytest.mark.parametrize("legacy_yml", [PRE_PROJECT_SECTION_YML, PRE_PROJECT_ID_YML])
def test_a_stored_revision_without_project_id_still_loads_under_its_rows_own_id(db, legacy_yml):
    old_revision = _store(db, legacy_yml)
    new_revision = _store(db, CURRENT_YML)
    assert new_revision != old_revision

    loader = AutomatonLoader(db)
    old = loader.load_at_revision(PROJECT_ID, old_revision)
    new = loader.load_at_revision(PROJECT_ID, new_revision)

    assert old.project_id == PROJECT_ID
    assert old.init_action.target == "a"
    assert new.init_action.target == "b"


def test_the_legacy_default_never_applies_outside_the_loader():
    """The same YAML through the builder directly — an upload, an editor
    save — is rejected exactly as before: only a row already in the
    Archive table earns the default."""
    with pytest.raises(ValueError, match="project"):
        AutomatonBuilder().build({"index.yml": PRE_PROJECT_SECTION_YML})
    with pytest.raises(ValueError, match="project.id"):
        AutomatonBuilder().build({"index.yml": PRE_PROJECT_ID_YML})


def test_a_stored_revision_that_no_longer_builds_names_itself(db):
    """Whatever else can go stale in a stored revision surfaces on some
    unrelated endpoint: the error must say which project/revision it is."""
    broken = _store(db, """
project:
  id: legacy_proj
init-action:
  target: a
  on-enter: celebrate()
states:
  a:
    ui-label: A
    contextual-prompt: hi
""")
    with pytest.raises(ValueError, match=rf"Project '{PROJECT_ID}', stored revision {broken}"):
        AutomatonLoader(db).load_at_revision(PROJECT_ID, broken)
