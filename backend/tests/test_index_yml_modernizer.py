"""A deprecated spelling a build can still read in full is not something
a human should have to retype. `project.talk-enabled` means
`services: {talk: <level>}` and nothing else, so "Edit project" rewrites
it on open, saves it, and says what it changed. Nothing else in the
backend repairs anything: a build reports the deprecation and leaves the
file exactly as written, so a loader stays a read path and an older
revision nobody opens stays byte-for-byte what it was.
"""
from __future__ import annotations

import pytest

from automaton.automaton_builder import AutomatonBuilder
from automaton.index_yml_modernizer import IndexYmlModernizer
from automaton.build_error import AutomatonBuildError
from project.archive.automaton_loader import AutomatonLoader

PROJECT_ID = "legacy_talk"

LEGACY_YML = """\
project:
  id: legacy_talk
  # keep me
  ui-label: Legacy
  talk-enabled: true
init-action:
  target: a
states:
  a:
    ui-label: A
    contextual-prompt: hi
"""

CURRENT_YML = LEGACY_YML.replace(
    "  talk-enabled: true\n", "  services:\n    talk: required\n",
)


def _store(db, yml: str) -> int:
    db.ensure_project(PROJECT_ID)
    db.save_project_files(PROJECT_ID, {"index.yml": yml.encode("utf-8")}, {"index.yml": "text/yaml"})
    db.publish_project(PROJECT_ID)
    return db.get_project_published_revision(PROJECT_ID)


@pytest.mark.parametrize("declared,level", [(True, "required"), (False, "disabled")])
def test_talk_enabled_is_rewritten_as_the_service_it_means(declared, level):
    modernized = IndexYmlModernizer().modernize(
        LEGACY_YML.replace("talk-enabled: true", f"talk-enabled: {str(declared).lower()}")
    )

    assert "talk-enabled" not in modernized.text
    assert f"talk: {level}" in modernized.text
    assert "# keep me" in modernized.text
    assert modernized.fixes == (f"project.talk-enabled → services: {{talk: {level}}}",)
    assert AutomatonBuilder().build({"index.yml": modernized.text}).services.as_raw() == {"talk": level}


def test_a_file_already_current_is_left_exactly_as_written():
    modernized = IndexYmlModernizer().modernize(CURRENT_YML)

    assert modernized.text == CURRENT_YML
    assert modernized.fixes == ()


def test_a_file_that_does_not_parse_is_left_to_the_builder():
    unparseable = "project: [\n"

    assert IndexYmlModernizer().modernize(unparseable).text == unparseable
    assert IndexYmlModernizer().modernize(unparseable).fixes == ()


def test_a_build_refuses_it_and_the_stored_file_is_left_alone(db):
    """A build has no memory of the format's own past: `talk-enabled` is
    a field `project` does not have, and that is all it says. It also
    never edits what it was given — the repair happens where a person is
    (see test_controller_index_yml_modernize.py), not on a read path."""
    revision = _store(db, LEGACY_YML)

    with pytest.raises(AutomatonBuildError, match="project.talk-enabled is not a field"):
        AutomatonLoader(db).load_at_revision(PROJECT_ID, revision)

    assert db.get_archive(PROJECT_ID, "index.yml", revision=revision).decode("utf-8") == LEGACY_YML


LEGACY_SCRIPTS_YML = """\
project:
  id: legacy_talk
init-action:
  target: a
actions:
  - &help
    name: help
    action-prompt: Give a hint.
states:
  a:
    ui-label: A
    contextual-prompt: hi
    chat: false
    on-enter: |
      actuator.notify('Arrived', 'Welcome')
    actions:
      - name: go
        target: b
        # keep me
        on-enter: |
          actuator.celebrate()
          actuator.send_mail(user.email, 'hi')
      - <<: *help
        target: b
      - name: old
        target: b
        actuator: actuator.prompt('x')
  b:
    ui-label: B
    contextual-prompt: there
env:
  counter:
    value: "0"
    ai-access: readonly
    ui-label: Counter
"""


def _automaton_of(text: str):
    return AutomatonBuilder().build({"index.yml": text})


def test_every_spelling_the_format_moved_past_is_settled_in_one_visit():
    """Each of them is its own rewrite, and one uncovers the next: a
    script moved off a state onto the actions that reach it arrives
    carrying `actuator.*`, which is itself a namespace with a
    replacement. The result builds — which is the whole contract, since
    a build refuses every one of these spellings."""
    modernized = IndexYmlModernizer().modernize(LEGACY_SCRIPTS_YML)
    automaton = _automaton_of(modernized.text)
    actions = {action.name: action for state in automaton.states.values() for action in state.actions}

    assert automaton.build_warnings == []
    assert automaton.states["a"].chat_enabled is False
    assert [key.name for key in automaton.env_keys] == ["counter"]
    assert actions["go"].on_exit == "chat.celebrate()"
    assert actions["go"].task == "task.send_mail(user.email, 'hi')"
    assert actions["help"].task == "task.prompt('Give a hint.')"
    assert actions["old"].task == "task.prompt('x')"
    assert "# keep me" in modernized.text
    assert modernized.text.count("<<:") == 1


def test_a_state_script_goes_to_every_action_that_reaches_the_state():
    """`on-enter` on a state ran once per entry into it, and every entry
    is an action targeting it — so each of them carries it now. Only the
    init-action reaches 'a' here, and the call lands in the field its own
    namespace belongs to."""
    automaton = _automaton_of(IndexYmlModernizer().modernize(LEGACY_SCRIPTS_YML).text)
    elsewhere = [
        action for state in automaton.states.values() for action in state.actions
        if action.target != "a"
    ]

    assert automaton.init_action.on_exit == "chat.notify('Arrived', 'Welcome')"
    assert elsewhere and not any(
        "Arrived" in (action.task or "") + (action.on_exit or "") for action in elsewhere
    )


def test_running_it_again_finds_nothing_and_changes_nothing():
    """Whatever it rewrote is spelled the way it looks for, so a second
    open reports no fix and leaves the file byte-for-byte. A rewrite its
    own detection still matched would claim a fix on every open and, where
    the fix appends, append again each time."""
    once = IndexYmlModernizer().modernize(LEGACY_SCRIPTS_YML)
    twice = IndexYmlModernizer().modernize(once.text)

    assert twice.fixes == ()
    assert twice.text == once.text


def test_an_action_nothing_can_settle_keeps_its_own_spelling():
    """`actuator.notify(..., actuator.prompt(...))` was one call reaching
    both the conversation and a service; today's format has no single
    line for that. The action is left exactly as written — including the
    field name, since renaming it alone would move a refusal rather than
    remove one — and the rest of the file is still repaired."""
    stuck = LEGACY_SCRIPTS_YML.replace(
        "        actuator: actuator.prompt('x')",
        "        on-enter: actuator.notify('Help', actuator.prompt('hint'))",
    )

    modernized = IndexYmlModernizer().modernize(stuck)

    assert "on-enter: actuator.notify('Help', actuator.prompt('hint'))" in modernized.text
    assert not any(fix.startswith("old:") for fix in modernized.fixes)
    assert "task.prompt('Give a hint.')" in modernized.text
