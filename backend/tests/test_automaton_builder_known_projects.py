"""AutomatonBuilder.build's `known_projects` parameter validates that a
referenced project/env key actually exists, beyond the placement check.
Deliberately just dicts/sets of plain strings — AutomatonBuilder is
tested in complete isolation, no Project/Db/ProjectService involved.
"""
from __future__ import annotations

import pytest

from automaton.automaton_builder import AutomatonBuilder

pytestmark = pytest.mark.contract

MINIMAL_STATES = "init-action:\n  target: a\nstates:\n  a:\n    contextual-prompt: hi\n"


def _build(trigger: str, known_projects: dict | None = None):
    yml = (
        "project:\n  id: test_project\n"
        "init-action:\n  target: a\n"
        "states:\n"
        "  a:\n"
        "    contextual-prompt: hi\n"
        "    actions:\n"
        "      - name: notice\n"
        "        target: a\n"
        f"        trigger: \"{trigger}\"\n"
    )
    return AutomatonBuilder().build({"index.yml": yml}, known_projects)


def test_known_projects_omitted_or_none_skips_the_check_entirely():
    """Referencing a nonexistent project.id, or an undeclared env key,
    is not a build-time error when known_projects isn't passed."""
    assert _build("automaton.nowhere.state == 'x'") is not None
    assert _build("automaton.nowhere.env.whatever == 1") is not None
    assert _build("automaton.nowhere.state == 'x'", known_projects=None) is not None


def test_a_referenced_project_id_must_be_known_and_a_non_env_chain_needs_no_env_key_at_all():
    """A non-.env chain never touches the env-key half of the check —
    only the project.id itself needs to exist."""
    with pytest.raises(ValueError, match="automaton.nowhere"):
        _build("automaton.nowhere.state == 'x'", known_projects={"real_project": frozenset()})
    assert _build("automaton.real_project.state == 'x'", known_projects={"real_project": frozenset()}) is not None
    assert _build("automaton.dep.state == 'x'", known_projects={"dep": frozenset()}) is not None


def test_every_referenced_env_key_must_be_in_the_named_projects_declared_set_after_the_project_itself_checks_out():
    """The project-existence check runs first, so the caller fixes the
    project reference before chasing an unresolvable key name."""
    with pytest.raises(ValueError, match="automaton.dep.env.missing_key"):
        _build("automaton.dep.env.missing_key == 1", known_projects={"dep": frozenset({"known_key"})})
    assert _build("automaton.dep.env.known_key == 1", known_projects={"dep": frozenset({"known_key"})}) is not None

    with pytest.raises(ValueError, match="automaton.nowhere"):
        _build("automaton.nowhere.env.whatever == 1", known_projects={"real_project": frozenset()})

    both = "automaton.dep.env.k1 == 1 and automaton.dep.env.k2 == 2"
    assert _build(both, known_projects={"dep": frozenset({"k1", "k2"})}) is not None
    with pytest.raises(ValueError, match="automaton.dep.env.k2"):
        _build(both, known_projects={"dep": frozenset({"k1"})})


@pytest.mark.parametrize(("yml", "expected"), [
    ("project:\n  id: dep_id\nenv:\n  k1:\n    value: \"'a'\"\n  k2:\n    value: \"'b'\"\n" + MINIMAL_STATES, ("dep_id", None, frozenset({"k1", "k2"}))),
    ("project:\n  id: dep_id\n  family: shared_family\n" + MINIMAL_STATES, ("dep_id", "shared_family", frozenset())),
    (MINIMAL_STATES, (None, None, frozenset())),
    ("project:\n  id: dep_id\n" + MINIMAL_STATES, ("dep_id", None, frozenset())),
    ("project:\n  id: 'not a valid id'\n" + MINIMAL_STATES, (None, None, frozenset())),
    ("project: not-a-mapping\n" + MINIMAL_STATES, (None, None, frozenset())),
], ids=["id-and-keys", "family", "no-project-section", "no-env-section", "invalid-id", "non-mapping-project"])
def test_read_declared_env_keys_reports_id_family_and_key_names_from_raw_yaml_never_raising(yml, expected):
    """AutomatonBuilder.read_declared_env_keys — a raw-YAML-only read used
    to populate known_projects for every other project, without a full
    build of it. It never raises on a malformed id, it just reports no
    id, so it never blocks validating a different project."""
    assert AutomatonBuilder.read_declared_env_keys(yml) == expected
