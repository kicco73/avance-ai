"""What a declared `attachments:` name becomes, and when.

The build resolves it to a stored path and verifies it exists; the turn
reads that path through whichever ProjectFiles the automaton composes.
Neither half knows about the other's world, which is the whole point: the
same declaration means an Archive row on the platform and a file under
data/ in a compiled package.
"""
from __future__ import annotations

import base64

import pytest

from automaton.automaton_builder import AutomatonBuilder
from automaton.builder.archive_resolver import ProjectArchives
from tracking.attachments import load_attachments
from tracking.project_files import PackageProjectFiles

INDEX = """
project:
  id: p
init-action:
  target: start
attachments:
  - notes.txt
general-prompt: hello
signals:
  progress:
    definition: how far along
    attachments:
      - notes.txt
states:
  start:
    contextual-prompt: go
    attachments:
      - logo.png
    actions:
      - name: go_on
        ui-label: On
        target: start
"""


def _automaton_at(revision: int):
    from automaton.automaton import Action, Automaton, State

    init_action = Action(name="init", ui_label="init", ui_button="", target="a")
    automaton = Automaton(
        init_action=init_action, states={"": State(key="", ui_label="", final=False, actions=[init_action])},
        general_prompt="", signals=[], general_attachments=(), autotracking_on_ai_message=False, project_id="p",
    )
    automaton.set_storage_location(revision)
    return automaton


def _contents(**extra) -> dict:
    return {
        "index.yml": INDEX,
        "behaviour/notes.txt": "a note\n",
        "logo.png": b"\x89PNG\r\n\x1a\n",
        **extra,
    }


def test_a_declared_basename_becomes_the_stored_path_everywhere_it_is_declared():
    automaton = AutomatonBuilder().build(_contents())

    assert automaton.general_attachments == ("behaviour/notes.txt",)
    assert automaton.signals[0].attachments == ("behaviour/notes.txt",)
    assert automaton.states["start"].attachments == ("logo.png",)


def test_the_automaton_carries_no_file_content_at_all():
    automaton = AutomatonBuilder().build(_contents())
    every_declaration = [
        automaton.general_attachments,
        automaton.signals[0].attachments,
        automaton.states["start"].attachments,
    ]
    assert all(isinstance(name, str) for names in every_declaration for name in names)


def test_a_name_the_project_does_not_carry_is_a_build_error():
    contents = _contents()
    del contents["behaviour/notes.txt"]
    with pytest.raises(Exception, match="notes.txt"):
        AutomatonBuilder().build(contents)


def test_an_ambiguous_basename_is_a_build_error_naming_both_candidates():
    archives = ProjectArchives({"a/x.txt": "", "b/x.txt": ""})
    with pytest.raises(ValueError, match="ambiguous"):
        archives.require(["x.txt"], "state 'start'")


def test_a_path_declared_in_full_wins_over_a_basename_match():
    archives = ProjectArchives({"x.txt": "top", "deep/x.txt": "nested"})
    assert archives.require(["x.txt"], "global") == ("x.txt",)
    assert archives.require(["deep/x.txt"], "global") == ("deep/x.txt",)


def test_a_turn_reads_the_declared_paths_as_text_or_base64_by_media_type(tmp_path):
    (tmp_path / "behaviour").mkdir()
    (tmp_path / "behaviour" / "notes.txt").write_text("a note\n")
    (tmp_path / "logo.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    automaton = AutomatonBuilder().build(_contents())

    archives = load_attachments(
        PackageProjectFiles(tmp_path),
        [*automaton.general_attachments, *automaton.states["start"].attachments],
    )

    assert [a.filename for a in archives] == ["behaviour/notes.txt", "logo.png"]
    assert archives[0].source == {"type": "text", "media_type": "text/plain", "data": "a note\n"}
    assert archives[1].source["type"] == "base64"
    # application/octet-stream, not image/png: what a file becomes is
    # decided from its name in one table (automaton.media_types), never
    # from what a reader reports — a stored project's Archive row would
    # say image/png and a package's data/ would not.
    assert archives[1].source["media_type"] == "application/octet-stream"
    assert base64.b64decode(archives[1].source["data"]) == b"\x89PNG\r\n\x1a\n"


def test_the_same_file_becomes_the_same_attachment_from_a_database_and_from_a_package(tmp_path, db):
    """The corner case this has to survive: a stored project's Archive row
    carries the content type the uploader stamped on it (text/csv), a
    package's data/ carries only the file. If either decided what to send
    from what its own reader reports, the same project would send the same
    attachment as text in one world and as base64 in the other."""
    from tracking.project_files import DbProjectFiles

    csv_bytes = b"code,city\nVY1,Paris\n"
    db.ensure_project("p")
    db.save_project_files("p", {"flights.csv": csv_bytes}, {"flights.csv": "text/csv"})
    stored = DbProjectFiles(db, _automaton_at(db.get_project_revision("p")))
    (tmp_path / "flights.csv").write_bytes(csv_bytes)

    assert stored.read("flights.csv")[1] == "text/csv"
    assert PackageProjectFiles(tmp_path).read("flights.csv")[1] == "text/plain"
    assert (
        load_attachments(stored, ["flights.csv"])
        == load_attachments(PackageProjectFiles(tmp_path), ["flights.csv"])
    )
    assert load_attachments(stored, ["flights.csv"])[0].source["type"] == "text"


def test_a_declared_file_that_is_gone_at_run_time_is_skipped_rather_than_failing_the_turn(tmp_path):
    """Existence was verified when the project was built, so this can only
    mean the project changed underneath an open session."""
    (tmp_path / "kept.txt").write_text("still here\n")

    archives = load_attachments(PackageProjectFiles(tmp_path), ["kept.txt", "vanished.txt"])

    assert [a.filename for a in archives] == ["kept.txt"]
