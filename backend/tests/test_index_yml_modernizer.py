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
    input-processor: ai
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


def test_a_stored_revision_nobody_opens_is_repaired_where_it_is(db):
    """What a product serves is the published revision, and nobody opens
    it. A build refuses a spelling the format moved past, so a revision
    written before the format tightened would take its project out of
    service until its author happened to visit — the loader asks the
    modernizer once, and stores the answer at that same revision."""
    revision = _store(db, LEGACY_YML)

    automaton = AutomatonLoader(db).load_at_revision(PROJECT_ID, revision)

    assert automaton.services.as_raw() == {"talk": "required"}
    stored = db.get_archive(PROJECT_ID, "index.yml", revision=revision).decode("utf-8")
    assert "talk-enabled" not in stored and "talk: required" in stored


def test_a_revision_it_cannot_settle_is_refused_and_left_as_written(db):
    revision = _store(db, LEGACY_YML.replace("talk-enabled: true", "whatever: true"))

    with pytest.raises(AutomatonBuildError, match="project.whatever is not a field"):
        AutomatonLoader(db).load_at_revision(PROJECT_ID, revision)

    assert "whatever: true" in db.get_archive(PROJECT_ID, "index.yml", revision=revision).decode("utf-8")


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
    input-processor: ai
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
    input-processor: ai
    contextual-prompt: there
env:
  counter:
    type: number
    value: "0"
    ai-access: readonly
    ui-label: Counter
  slot:
    type: choice
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

    assert automaton.states["a"].chat_enabled is False
    assert [(key.name, key.type) for key in automaton.env_keys] == [("counter", "number"), ("slot", "list")]
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


LEGACY_AI_MEMORY_YML = """\
project:
  id: legacy_memory
init-action:
  target: a
states:
  a:
    input-processor: ai
    contextual-prompt: hi
    ai-memory-strategy: keep
  b:
    input-processor: ai
    contextual-prompt: bye
    ai-memory-strategy: clear
"""


@pytest.mark.parametrize("old,new", [("keep", "global"), ("clear", "local")])
def test_ai_memory_strategy_is_rewritten_as_the_scope_it_means(old, new):
    modernized = IndexYmlModernizer().modernize(
        LEGACY_AI_MEMORY_YML.replace("ai-memory-strategy: keep\n", f"ai-memory-strategy: {old}\n")
    )

    assert "ai-memory-strategy" not in modernized.text
    assert f"ai-memory-scope: {new}" in modernized.text
    automaton = AutomatonBuilder().build({"index.yml": modernized.text})
    assert automaton.states["a"].ai_memory_scope == new


def test_a_stored_revision_with_the_old_memory_field_is_repaired_where_it_is(db):
    revision = _store(db, LEGACY_AI_MEMORY_YML)

    automaton = AutomatonLoader(db).load_at_revision(PROJECT_ID, revision)

    assert automaton.states["a"].ai_memory_scope == "global"
    assert automaton.states["b"].ai_memory_scope == "local"
    stored = db.get_archive(PROJECT_ID, "index.yml", revision=revision).decode("utf-8")
    assert "ai-memory-strategy" not in stored and "ai-memory-scope: global" in stored


NO_PROCESSOR_YML = """\
project:
  id: legacy_talk
init-action:
  target: a
states:
  a:
    ui-label: A
    # keep me
    contextual-prompt: hi
  b:
    ui-label: B
    input-processor: system
"""


def test_a_state_that_does_not_say_who_answers_answered_through_the_model():
    """Before `input-processor` existed every state was the model's, so
    the field's absence is a fact about the file's age, not a choice to
    make on the author's behalf — the one key the modernizer adds."""
    modernized = IndexYmlModernizer().modernize(NO_PROCESSOR_YML)

    assert modernized.fixes == ("a: input-processor: ai",)
    assert "# keep me" in modernized.text
    automaton = AutomatonBuilder().build({"index.yml": modernized.text})
    assert automaton.states["a"].input_processor == "ai"
    assert automaton.states["b"].input_processor == "system"
    assert IndexYmlModernizer().modernize(modernized.text).fixes == ()


LEGACY_FIXED_MESSAGE_YML = """\
project:
  id: legacy_talk
init-action:
  target: a
states:
  a:
    ui-label: A
    input-processor: ai
    contextual-prompt: hi
    actions:
      - name: go
        target: done
  done:
    ui-label: Done
    input-processor: ai
    chat-enabled: false
    # keep me
    fixed-message: |
      Thanks, we're done here.
"""


def test_a_fixed_message_state_becomes_system_and_a_chat_write_on_every_action_that_reaches_it():
    """`fixed-message` said the same thing on every entry, no model call
    involved — today that's `input-processor: system`, with the text
    written by `chat.write(...)` in the on-exit of whatever reaches it."""
    modernized = IndexYmlModernizer().modernize(LEGACY_FIXED_MESSAGE_YML)

    assert modernized.fixes == (
        "done: fixed-message → input-processor: system + on-exit chat.write(...) of the actions that reach it",
    )
    assert "fixed-message" not in modernized.text
    assert "# keep me" in modernized.text
    automaton = AutomatonBuilder().build({"index.yml": modernized.text})
    assert automaton.states["done"].input_processor == "system"
    actions = {action.name: action for state in automaton.states.values() for action in state.actions}
    assert actions["go"].on_exit == "chat.write(\"Thanks, we're done here.\")"
    assert IndexYmlModernizer().modernize(modernized.text).fixes == ()


def test_every_stored_revision_is_settled_at_boot_not_at_the_first_visit(db):
    from project.archive.index_yml_migration import modernize_stored_revisions

    revision = _store(db, NO_PROCESSOR_YML)
    broken = "broken_one"
    db.ensure_project(broken)
    db.save_project_files(broken, {"index.yml": LEGACY_YML.replace("talk-enabled: true", "whatever: true").encode("utf-8")}, {"index.yml": "text/yaml"})
    db.publish_project(broken)

    assert modernize_stored_revisions(db) == {PROJECT_ID}

    stored = db.get_archive(PROJECT_ID, "index.yml", revision=revision).decode("utf-8")
    assert "input-processor: ai" in stored
    assert "whatever: true" in db.get_archive(broken, "index.yml").decode("utf-8")
    assert modernize_stored_revisions(db) == set()


OLD_FILE_NAMES_YML = """\
project:
  id: legacy_talk
init-action:
  target: a
states:
  a:
    input-processor: ai
    contextual-prompt: hi
    actions:
      - name: go
        target: a
        on-exit: |
          chat.show_media(media.Manuel_neutro.url())
          chat.show(attachment.read('Template informe.md'))
"""
OLD_FILE_NAMES_FILES = {"media/Manuel_neutro.png": b"\x89PNG", "behaviour/Template informe.md": b"Hola"}


def test_a_stored_revision_naming_files_the_old_way_is_repaired_where_it_is(db):
    db.ensure_project(PROJECT_ID)
    db.save_project_files(
        PROJECT_ID, {"index.yml": OLD_FILE_NAMES_YML.encode("utf-8"), **OLD_FILE_NAMES_FILES},
        {"index.yml": "text/yaml", "media/Manuel_neutro.png": "image/png", "behaviour/Template informe.md": "text/markdown"},
    )
    db.publish_project(PROJECT_ID)
    revision = db.get_project_published_revision(PROJECT_ID)

    AutomatonLoader(db).load_at_revision(PROJECT_ID, revision)

    stored = db.get_archive(PROJECT_ID, "index.yml", revision=revision).decode("utf-8")
    assert "media.manuel_neutro.url()" in stored
    assert "attachment.template_informe.read()" in stored
