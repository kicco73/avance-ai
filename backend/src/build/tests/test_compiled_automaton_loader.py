"""Which automaton a project gets served, and why.

The loader's whole job is a decision — compiled package or Archive rows —
so what these pin is the decision, in every shape it takes: the right
revision, the wrong one, no package at all, and a package that lies about
which revision it came from.
"""
from __future__ import annotations

import sys

import pytest

from automaton.automaton import CompiledAutomaton
from project.archive.packages import PackageError, import_automaton, package_dir, staging_dir
from build.build_service import BuildService, module_name_for
from build.compiler import CompileError, compile_contents
from db import Db
from project.archive.automaton_loader import AutomatonLoader
from build.compiled_automaton_loader import CompiledAutomatonLoader

PROJECT_ID = "demo"
INDEX = """
project:
  id: demo
init-action:
  target: start
general-prompt: hello
states:
  start:
    input-processor: ai
    contextual-prompt: go
"""


def _publish(db: Db, index: str = INDEX) -> int:
    db.ensure_project(PROJECT_ID)
    db.save_project_files(PROJECT_ID, {"index.yml": index.encode()}, {"index.yml": "text/yaml"})
    db.publish_project(PROJECT_ID)
    return db.get_project_revision(PROJECT_ID)


def _compile_into(apps_dir, revision: int, *, index: str = INDEX, declared_revision: int | None = None) -> None:
    """A package built straight into its final place — what BuildService
    produces, without going through it."""
    module_name = module_name_for(PROJECT_ID)
    built = compile_contents(
        {"index.yml": index}, module_name, apps_dir,
        declared_revision if declared_revision is not None else revision,
    )
    built.rename(package_dir(apps_dir, module_name, revision))


def _loader(db: Db, apps_dir) -> CompiledAutomatonLoader:
    return CompiledAutomatonLoader(db, apps_dir)


def test_the_published_revision_is_served_from_the_package_when_one_matches(db, tmp_path):
    revision = _publish(db)
    _compile_into(tmp_path, revision)

    automaton = _loader(db, tmp_path).load_at_revision(PROJECT_ID, revision)

    assert isinstance(automaton, CompiledAutomaton)
    assert automaton.archives_dir is not None
    assert automaton.revision == revision


def test_without_a_package_the_ordinary_loader_answers(db, tmp_path):
    revision = _publish(db)

    automaton = _loader(db, tmp_path).load_at_revision(PROJECT_ID, revision)

    assert type(automaton).__name__ == "Automaton"
    assert automaton.revision == revision


def test_load_never_serves_compiled_even_when_the_draft_matches_the_published_revision(db, tmp_path):
    revision = _publish(db)
    _compile_into(tmp_path, revision)

    assert type(_loader(db, tmp_path).load(PROJECT_ID)).__name__ == "Automaton"
    assert isinstance(_loader(db, tmp_path).load_at_revision(PROJECT_ID, revision), CompiledAutomaton)


def test_a_revision_that_is_not_the_published_one_is_never_served_compiled(db, tmp_path):
    """A draft changes under the editor's hands; an older revision a
    session is pinned to had a build at most in the past."""
    published = _publish(db)
    _compile_into(tmp_path, published)
    db.save_project_files(PROJECT_ID, {"index.yml": INDEX.encode()}, {"index.yml": "text/yaml"})
    draft = db.get_project_revision(PROJECT_ID)
    assert draft != published

    assert type(_loader(db, tmp_path).load_at_revision(PROJECT_ID, draft)).__name__ == "Automaton"
    assert isinstance(_loader(db, tmp_path).load_at_revision(PROJECT_ID, published), CompiledAutomaton)


def test_a_package_compiled_from_another_revision_is_refused_and_the_project_still_loads(db, tmp_path, caplog):
    """The directory name is a convenience; STORAGE_REVISION is the claim
    the package itself makes, and the only one trusted."""
    revision = _publish(db)
    _compile_into(tmp_path, revision, declared_revision=revision + 7)

    automaton = _loader(db, tmp_path).load_at_revision(PROJECT_ID, revision)

    assert type(automaton).__name__ == "Automaton", "a wrong package degrades, never fails the request"


def test_a_package_that_does_not_import_degrades_rather_than_breaking_the_project(db, tmp_path):
    revision = _publish(db)
    _compile_into(tmp_path, revision)
    broken = package_dir(tmp_path, module_name_for(PROJECT_ID), revision) / "__init__.py"
    broken.write_text("this is not python(")

    assert type(_loader(db, tmp_path).load_at_revision(PROJECT_ID, revision)).__name__ == "Automaton"


def test_the_second_load_comes_from_the_cache_rather_than_a_second_import(db, tmp_path):
    revision = _publish(db)
    _compile_into(tmp_path, revision)
    loader = _loader(db, tmp_path)

    first = loader.load_at_revision(PROJECT_ID, revision)
    import shutil
    shutil.rmtree(package_dir(tmp_path, module_name_for(PROJECT_ID), revision))

    assert loader.load_at_revision(PROJECT_ID, revision) is first


def test_two_revisions_of_one_project_can_be_imported_at_once_without_serving_each_other(db, tmp_path):
    """A session pinned to the older revision and a new one on the newer
    are both live; sys.modules must not let one answer for the other."""
    first_revision = _publish(db)
    _compile_into(tmp_path, first_revision, index=INDEX)
    second_index = INDEX.replace("general-prompt: hello", "general-prompt: goodbye")
    _publish(db, second_index)
    second_revision = db.get_project_revision(PROJECT_ID)
    _compile_into(tmp_path, second_revision, index=second_index)

    older = import_automaton(package_dir(tmp_path, module_name_for(PROJECT_ID), first_revision), PROJECT_ID, first_revision)
    newer = import_automaton(package_dir(tmp_path, module_name_for(PROJECT_ID), second_revision), PROJECT_ID, second_revision)

    assert older.general_prompt == "hello"
    assert newer.general_prompt == "goodbye"


def test_everything_other_than_load_and_load_at_revision_is_the_ordinary_loader(db, tmp_path):
    loader = _loader(db, tmp_path)
    assert isinstance(loader, AutomatonLoader)
    overridden = {
        name for name in vars(CompiledAutomatonLoader) if not name.startswith("_") and callable(getattr(loader, name))
    }
    assert overridden == {"load", "load_at_revision"}


ALL_SIGNALS_INDEX = """
project:
  id: demo
init-action:
  target: start
general-prompt: hello
signals:
  mood:
    definition: how the user feels
  pace:
    definition: how fast the user goes
states:
  start:
    signal-tracking-strategy: all
    input-processor: ai
    contextual-prompt: go
    actions:
      - name: advance
        ui-label: Advance
        target: end
        trigger: signal.mood > 50
  end:
    input-processor: ai
    contextual-prompt: bye
"""


def test_a_compiled_state_keeps_its_ai_memory_scope(db, tmp_path):
    index = ALL_SIGNALS_INDEX.replace("    signal-tracking-strategy: all\n", "    ai-memory-scope: local\n")
    revision = _publish(db, index)
    _compile_into(tmp_path, revision, index=index)

    compiled = _loader(db, tmp_path).load_at_revision(PROJECT_ID, revision)
    interpreted = AutomatonLoader(db).load_at_revision(PROJECT_ID, revision)

    assert compiled.states["start"] == interpreted.states["start"]
    assert compiled.states["start"].ai_memory_scope == "local"
    assert compiled.states["end"].ai_memory_scope == "none"


def test_a_compiled_state_keeps_its_signal_tracking_strategy_and_tracks_the_same_signals(db, tmp_path):
    revision = _publish(db, ALL_SIGNALS_INDEX)
    _compile_into(tmp_path, revision, index=ALL_SIGNALS_INDEX)

    compiled = _loader(db, tmp_path).load_at_revision(PROJECT_ID, revision)
    interpreted = AutomatonLoader(db).load_at_revision(PROJECT_ID, revision)

    assert isinstance(compiled, CompiledAutomaton)
    assert compiled.states["start"] == interpreted.states["start"]
    assert compiled.states["start"].signal_tracking_strategy == "all"
    assert compiled.tracked_signal_names("start") == interpreted.tracked_signal_names("start") == {"mood", "pace"}
    assert compiled.tracked_signal_names("end") == interpreted.tracked_signal_names("end") == set()


def test_the_draft_is_never_served_compiled_even_right_after_a_publish_leaves_the_two_revisions_equal(db, tmp_path):
    """The Test chat panel must always run the dynamic draft, never a
    build — a compiled package exists only for a *published* revision,
    and right after a publish the draft's own revision number is that
    same number, which `load_at_revision` alone cannot tell apart from
    "give me this exact package". `ProjectInspector.get_draft_automaton`
    (what a 'test' session's own automaton lookup goes through, see
    ProjectInspector.get_automaton_for_session/get_automaton_and_state)
    is the seam that keeps that promise."""
    from project.inspector import ProjectInspector

    revision = _publish(db)
    _compile_into(tmp_path, revision)
    loader = _loader(db, tmp_path)
    live_serves = loader.load_at_revision(PROJECT_ID, revision)
    assert isinstance(live_serves, CompiledAutomaton), (
        "sanity check: a package really is being served for this revision"
    )

    automaton = ProjectInspector(db, loader).get_draft_automaton(PROJECT_ID)

    assert type(automaton).__name__ == "Automaton"
    assert loader.load_at_revision(PROJECT_ID, revision) is live_serves, (
        "the draft lookup must never touch the compiled/live cache — "
        "a live request's own cached instance survives it untouched"
    )


def test_the_draft_is_never_stale_even_though_it_is_never_cached(db, tmp_path):
    """A project's *own* revision row is rewritten in place by every edit
    but the first after a publish (see db/projects.py's
    _ensure_draft_revision: only that first edit forks to revision + 1;
    every edit after that keeps the same number). A second edit landing
    on that same, already-forked revision must show up on the very next
    draft read — there is no cache anywhere on this path for it to be
    stale in."""
    from project.inspector import ProjectInspector

    published = _publish(db)
    loader = _loader(db, tmp_path)

    db.save_project_files(
        PROJECT_ID, {"index.yml": INDEX.replace("hello", "first edit").encode()}, {"index.yml": "text/yaml"},
    )
    forked = db.get_project_revision(PROJECT_ID)
    assert forked != published, "the first edit after a publish forks a new revision"
    first = ProjectInspector(db, loader).get_draft_automaton(PROJECT_ID)
    assert first.general_prompt == "first edit"

    db.save_project_files(
        PROJECT_ID, {"index.yml": INDEX.replace("hello", "second edit").encode()}, {"index.yml": "text/yaml"},
    )
    assert db.get_project_revision(PROJECT_ID) == forked, "a further edit rewrites the same revision in place"

    second = ProjectInspector(db, loader).get_draft_automaton(PROJECT_ID)
    assert second.general_prompt == "second edit"
