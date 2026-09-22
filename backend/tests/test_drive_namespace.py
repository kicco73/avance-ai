"""The `drive` namespace: a task script's own file space for the person
it runs for.

Every test here drives a real `task:` script through
Automaton.render_task_script — the way an ActionTask runs one — and
observes through the public Db surface (read_drive_file/
list_drive_files), never the Drive model.

What it defends: a drive belongs to (project, person), it starts empty,
it survives what happens to the project around it, and it never appears
among the project's own files.
"""
from __future__ import annotations

import pytest

from conftest import RecordedMessages
from automaton.automaton_builder import AutomatonBuilder
from automaton.choice import ChoiceSelection
from automaton.identifier_registry import IdentifierRegistry
from db import Db
from metrics.metric_service import MetricService
from system.bus import OUTPUT_DRIVE
from system.web_session import WebSession
from tracking.env import Env
from tracking.evaluation_scope import EvaluationScopeBuilder
from tracking.fixed_project_context import FixedProjectContext
from tracking.session_facts import SessionFacts
from tracking.user_facts import UserFacts

pytestmark = pytest.mark.contract

PROJECT = "reports"
OTHER_USER = "other@example.com"

INDEX_YML = """
project:
  id: reports
  ui-label: Reports
init-action:
  target: a
states:
  a:
    ui-label: A
    input-processor: ai
    contextual-prompt: hi
    actions:
      - name: go
        target: b
        task: |
          {task}
  b:
    ui-label: B
    input-processor: ai
    contextual-prompt: there
"""


def _yml(task: str) -> str:
    indented = ("\n" + " " * 10).join(task.splitlines())
    return INDEX_YML.replace("{task}", indented)


def _publish(db: Db, index_yml: str = None) -> None:
    db.ensure_project(PROJECT)
    db.save_project_files(
        PROJECT, {"index.yml": (index_yml or _yml("drive.write('x.md', 'x')")).encode("utf-8")},
        {"index.yml": "text/yaml"},
    )
    db.publish_project(PROJECT)


def _run(
    db: Db, script: str, username: str = "user", session_id: int | None = None,
) -> list[tuple[str, Exception]]:
    """`script` as a task script, against a task-view scope built the way
    TrackingEngine builds one. Returns the statements that failed."""
    automaton = AutomatonBuilder().build({"index.yml": _yml(script)})
    automaton.project_id = PROJECT
    context = FixedProjectContext(automaton=automaton, project_id=PROJECT)
    with WebSession().impersonate(username):
        scope = EvaluationScopeBuilder(
            Env(), MetricService(db, context), SessionFacts(db, context), UserFacts(db), db,
        ).build(automaton, "a", {}, ChoiceSelection.NONE, session_id=session_id)
        outcome = automaton.render_task_script(script, scope.for_task(action_name="go"))
    return list(outcome.failures)


def test_a_task_script_writes_a_report_and_a_later_one_reads_it_back(db):
    _publish(db)

    assert _run(db, "drive.write('reports/last.md', 'ciao')") == []

    assert _run(db, "kept = drive.read('reports/last.md')\ndrive.write('echo.md', kept)") == []
    content, content_type = db.read_drive_file(PROJECT, "user", "echo.md")
    assert content.decode("utf-8") == "ciao"
    assert db.read_drive_file(PROJECT, "user", "reports/last.md")[1] == "text/markdown"


def test_bytes_are_written_and_read_back_verbatim_never_as_text(db):
    _publish(db)

    assert _run(db, "drive.write('logo.png', b'\\x89PNG\\r\\n')") == []

    content, content_type = db.read_drive_file(PROJECT, "user", "logo.png")
    assert content == b"\x89PNG\r\n"
    assert content_type == "image/png"

    assert _run(db, "drive.write('seen.md', 'yes' if drive.read('logo.png') == b'\\x89PNG\\r\\n' else 'no')") == []
    assert db.read_drive_file(PROJECT, "user", "seen.md")[0] == b"yes"


def test_a_value_that_is_neither_text_nor_bytes_is_refused_untouched(db):
    _publish(db)

    failures = _run(db, "drive.write('x.md', 3)")

    assert len(failures) == 1
    assert db.list_drive_files(PROJECT, "user") == []


def test_two_people_never_see_each_others_files(db):
    _publish(db)
    db.get_or_create_user("test", "sub-other", OTHER_USER, "Other", None)

    _run(db, "drive.write('reports/last.md', 'mio')")
    _run(db, "drive.write('reports/last.md', 'tuo')", username=OTHER_USER)

    assert db.read_drive_file(PROJECT, "user", "reports/last.md")[0] == b"mio"
    assert db.read_drive_file(PROJECT, OTHER_USER, "reports/last.md")[0] == b"tuo"
    assert [entry["path"] for entry in db.list_drive_files(PROJECT, "user")] == ["reports/last.md"]


def test_reading_a_file_nobody_wrote_gives_the_empty_string(db):
    _publish(db)

    assert _run(db, "drive.write('seen.md', drive.read('reports/never.md') + 'fine')") == []

    assert db.read_drive_file(PROJECT, "user", "seen.md")[0] == b"fine"


def test_a_leading_slash_names_the_same_file_and_writing_twice_replaces_it(db):
    _publish(db)

    _run(db, "drive.write('reports/last.md', 'prima')")
    _run(db, "drive.write('/reports/last.md', 'dopo')")

    assert db.read_drive_file(PROJECT, "user", "reports/last.md")[0] == b"dopo"
    assert len(db.list_drive_files(PROJECT, "user")) == 1


def test_a_path_that_climbs_out_is_refused(db):
    _publish(db)

    failures = _run(db, "drive.write('../elsewhere.md', 'no')")

    assert len(failures) == 1
    assert db.list_drive_files(PROJECT, "user") == []


def test_list_answers_the_paths_under_a_prefix_and_delete_removes_one(db):
    _publish(db)
    _run(db, "drive.write('reports/a.md', '1')\ndrive.write('reports/b.md', '2')\ndrive.write('notes/c.md', '3')")

    assert _run(db, "drive.write('found.txt', ', '.join(drive.list('reports/')))") == []
    assert db.read_drive_file(PROJECT, "user", "found.txt")[0] == b"reports/a.md, reports/b.md"

    _run(db, "drive.delete('reports/a.md')")
    assert [entry["path"] for entry in db.list_drive_files(PROJECT, "user", "reports/")] == ["reports/b.md"]


def test_the_drive_survives_a_new_draft_and_a_publish_of_the_project(db):
    _publish(db)
    _run(db, "drive.write('reports/last.md', 'ciao')")

    db.save_project_files(PROJECT, {"behaviour/note.txt": b"edited"}, {"behaviour/note.txt": "text/plain"})
    db.publish_project(PROJECT)

    assert db.read_drive_file(PROJECT, "user", "reports/last.md")[0] == b"ciao"


def test_the_drive_is_not_among_the_projects_own_files(db):
    _publish(db)
    _run(db, "drive.write('reports/last.md', 'ciao')")

    assert db.list_archives(PROJECT) == ["index.yml"]
    assert db.get_archive(PROJECT, "reports/last.md") is None


def test_erasing_a_person_takes_their_drive_with_it(db):
    _publish(db)
    db.get_or_create_user("test", "sub-other", OTHER_USER, "Other", None)
    _run(db, "drive.write('reports/last.md', 'mio')")
    _run(db, "drive.write('reports/last.md', 'tuo')", username=OTHER_USER)

    db.erase_user_data(OTHER_USER)

    assert db.read_drive_file(PROJECT, OTHER_USER, "reports/last.md") is None
    assert db.read_drive_file(PROJECT, "user", "reports/last.md")[0] == b"mio"


def test_a_merge_sums_both_drives_and_auto_renames_a_clashing_path(db):
    _publish(db)
    db.get_or_create_user("test", "sub-other", OTHER_USER, "Other", None)
    _run(db, "drive.write('reports/last.md', 'mio')\ndrive.write('reports/only-mine.md', 'x')")
    _run(db, "drive.write('reports/last.md', 'tuo')\ndrive.write('reports/only-yours.md', 'y')", username=OTHER_USER)

    db.merge_user_accounts("user", OTHER_USER, "34600000001")

    assert db.read_drive_file(PROJECT, "user", "reports/last.md")[0] == b"mio"
    assert db.read_drive_file(PROJECT, "user", "reports/last (merged).md")[0] == b"tuo"
    assert [entry["path"] for entry in db.list_drive_files(PROJECT, "user")] == [
        "reports/last (merged).md", "reports/last.md", "reports/only-mine.md", "reports/only-yours.md",
    ]


def test_a_write_publishes_output_drive_with_no_session_on_the_envelope(db):
    _publish(db)
    recorded = RecordedMessages(OUTPUT_DRIVE)

    assert _run(db, "drive.write('reports/last.md', 'ciao')") == []

    (message,) = recorded.of_type(OUTPUT_DRIVE)
    assert message.username == "user"
    assert message.project_id == PROJECT
    assert message.session_id is None
    assert message.body == {"path": "reports/last.md"}


def test_a_write_tied_to_a_firing_session_carries_it_on_the_envelope(db):
    _publish(db)
    session_id = db.create_chat_session("user", PROJECT, revision=0, type="live")
    recorded = RecordedMessages(OUTPUT_DRIVE)

    assert _run(db, "drive.write('reports/last.md', 'ciao')", session_id=session_id) == []

    (message,) = recorded.of_type(OUTPUT_DRIVE)
    assert message.session_id == session_id


def test_a_failed_write_publishes_nothing(db):
    _publish(db)
    recorded = RecordedMessages(OUTPUT_DRIVE)

    _run(db, "drive.write('x.md', 3)")

    assert recorded.of_type(OUTPUT_DRIVE) == []


def test_drive_is_a_task_only_namespace(db):
    trigger_registry = IdentifierRegistry.for_triggers({"drive": {"read": ""}})
    on_exit_registry = IdentifierRegistry.for_on_exit({"drive": {"read": ""}})

    assert "drive" not in trigger_registry
    assert "drive" not in on_exit_registry


def test_a_drive_call_with_the_wrong_number_of_arguments_is_a_build_error(db):
    with pytest.raises(ValueError, match=r"drive.write\(...\) missing a required argument"):
        AutomatonBuilder().build({"index.yml": _yml("drive.write('only-one.md')")})

    AutomatonBuilder().build({"index.yml": _yml("drive.list()")})
