"""AutomatonBuilder.build's own `known_projects` parameter (Prompt 10) —
a correction to Prompt 6/8's own static validation, which only ever
checked that an automaton.* reference sat in a self-loop action's own
trigger, never whether the project/env key it names actually exists.
Deliberately just dicts/sets of plain strings in and out — this module
tests AutomatonBuilder in complete isolation, no Project/Db/
ProjectService involved at all (see ProjectService._known_projects_env_
keys, the one real caller, covered separately in test_project_id_
metadata.py).
"""
from __future__ import annotations

import pytest

from automaton.automaton_builder import AutomatonBuilder

pytestmark = pytest.mark.contract

MINIMAL_STATES = "init-action:\n  target: a\nstates:\n  a:\n    contextual-prompt: hi\n"


def _build(trigger: str, known_projects: dict | None = None):
    yml = (
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


def test_known_projects_omitted_skips_the_check_entirely():
    """Behavior unchanged for every caller that doesn't pass this —
    referencing a nonexistent project.id, or an undeclared env key on
    one, was never a build-time error before this parameter existed."""
    automaton = _build("automaton.nowhere.state == 'x'")
    assert automaton is not None

    automaton = _build("automaton.nowhere.env.whatever == 1")
    assert automaton is not None


def test_known_projects_none_explicitly_also_skips_the_check():
    automaton = _build("automaton.nowhere.state == 'x'", known_projects=None)
    assert automaton is not None


def test_rejects_a_project_id_absent_from_known_projects():
    with pytest.raises(ValueError, match="automaton.nowhere"):
        _build("automaton.nowhere.state == 'x'", known_projects={"real_project": frozenset()})


def test_accepts_a_project_id_present_in_known_projects():
    automaton = _build("automaton.real_project.state == 'x'", known_projects={"real_project": frozenset()})
    assert automaton is not None


def test_rejects_an_env_key_absent_from_the_named_projects_declared_set():
    with pytest.raises(ValueError, match="automaton.dep.env.missing_key"):
        _build("automaton.dep.env.missing_key == 1", known_projects={"dep": frozenset({"known_key"})})


def test_accepts_an_env_key_present_in_the_named_projects_declared_set():
    automaton = _build("automaton.dep.env.known_key == 1", known_projects={"dep": frozenset({"known_key"})})
    assert automaton is not None


def test_an_unknown_env_key_on_an_unknown_project_reports_the_project_not_the_key():
    """The project-existence check runs first — an env key check against
    a project that isn't even in known_projects at all would be a
    confusing, secondary error; the caller should fix the project
    reference first, not chase a key name that was never going to
    resolve either way."""
    with pytest.raises(ValueError, match="automaton.nowhere"):
        _build("automaton.nowhere.env.whatever == 1", known_projects={"real_project": frozenset()})


def test_multiple_references_in_one_trigger_are_all_checked():
    automaton = _build(
        "automaton.dep.env.k1 == 1 and automaton.dep.env.k2 == 2",
        known_projects={"dep": frozenset({"k1", "k2"})},
    )
    assert automaton is not None

    with pytest.raises(ValueError, match="automaton.dep.env.k2"):
        _build(
            "automaton.dep.env.k1 == 1 and automaton.dep.env.k2 == 2",
            known_projects={"dep": frozenset({"k1"})},
        )


def test_a_bare_automaton_dot_project_reference_with_no_env_needs_no_env_key_at_all():
    """automaton.<id>.state (or any non-.env chain) never touches the
    env-key half of the check — only the project.id itself needs to
    exist."""
    automaton = _build("automaton.dep.state == 'x'", known_projects={"dep": frozenset()})
    assert automaton is not None


class TestReadDeclaredEnvKeys:
    """AutomatonBuilder.read_declared_env_keys — the raw-YAML-only read
    ProjectService._known_projects_env_keys uses to populate
    known_projects for every *other* project, without paying for (or
    risking) a full build of it."""

    def test_reads_id_and_declared_env_key_names(self):
        yml = "project:\n  id: dep_id\nenv:\n  k1:\n    value: \"'a'\"\n  k2:\n    value: \"'b'\"\n" + MINIMAL_STATES
        project_id, env_keys = AutomatonBuilder.read_declared_env_keys(yml)
        assert project_id == "dep_id"
        assert env_keys == frozenset({"k1", "k2"})

    def test_no_project_section_reports_no_id(self):
        project_id, env_keys = AutomatonBuilder.read_declared_env_keys(MINIMAL_STATES)
        assert project_id is None
        assert env_keys == frozenset()

    def test_no_env_section_reports_an_empty_set(self):
        yml = "project:\n  id: dep_id\n" + MINIMAL_STATES
        project_id, env_keys = AutomatonBuilder.read_declared_env_keys(yml)
        assert project_id == "dep_id"
        assert env_keys == frozenset()

    def test_an_invalid_identifier_id_reports_no_id(self):
        """Same grammar build() itself would reject (see _build_project_
        metadata) — but this method never raises, it just reports nothing
        to reference this project by, so one other project's own
        malformed id never blocks validating a different one."""
        yml = "project:\n  id: 'not a valid id'\n" + MINIMAL_STATES
        project_id, _ = AutomatonBuilder.read_declared_env_keys(yml)
        assert project_id is None

    def test_a_non_mapping_project_section_reports_no_id_rather_than_raising(self):
        yml = "project: not-a-mapping\n" + MINIMAL_STATES
        project_id, env_keys = AutomatonBuilder.read_declared_env_keys(yml)
        assert project_id is None
        assert env_keys == frozenset()
