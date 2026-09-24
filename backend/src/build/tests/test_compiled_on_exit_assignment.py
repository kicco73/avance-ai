"""A compiled package must answer an on-exit assignment the same way
the interpreted automaton does — see COMPILED_AUTOMATON.md's fifth
platform-change incident: `Compiler._collect_sources` once put an
on-exit assignment's RHS in the wrong table, so every compiled project
with one raised "no compiled statement" on every run of the action.
"""
from __future__ import annotations

from automaton.automaton import Action
from automaton.scope import EvaluationScope
from build.build_service import module_name_for
from build.compiler import compile_contents
from db import Db
from project.archive.packages import package_dir
from build.compiled_automaton_loader import CompiledAutomatonLoader

PROJECT_ID = "on_exit_demo"
INDEX = """
project:
  id: on_exit_demo
init-action:
  target: start
general-prompt: hello
states:
  start:
    input-processor: ai
    contextual-prompt: go
    actions:
      - name: go
        ui-label: Go
        target: start
        on-exit: |
          local = 1 + 1
          env.counter = local + env.counter
env:
  counter:
    type: number
"""


def test_a_compiled_on_exit_assignment_evaluates_without_a_table_miss(db: Db, tmp_path):
    db.ensure_project(PROJECT_ID)
    db.save_project_files(PROJECT_ID, {"index.yml": INDEX.encode()}, {"index.yml": "text/yaml"})
    db.publish_project(PROJECT_ID)
    revision = db.get_project_revision(PROJECT_ID)

    module_name = module_name_for(PROJECT_ID)
    built = compile_contents({"index.yml": INDEX}, module_name, tmp_path, revision)
    built.rename(package_dir(tmp_path, module_name, revision))

    automaton = CompiledAutomatonLoader(db, tmp_path).load_at_revision(PROJECT_ID, revision)
    action = automaton.states["start"].actions[0]
    scope = EvaluationScope({"env": {"counter": 5}}, automaton=automaton, state_key="start")

    updates, _chat_snippets, failures = automaton.eval_action_on_exit(action, scope)

    assert failures == ()
    assert updates == {"counter": 7}


IF_INDEX = INDEX.replace(
    "          local = 1 + 1\n          env.counter = local + env.counter\n",
    "          if env.counter > 3:\n            env.counter = env.counter * 10\n          else:\n            env.counter = 0\n",
).replace("on_exit_demo", "on_exit_if_demo")


def test_a_compiled_on_exit_if_runs_the_branch_its_condition_picks(db: Db, tmp_path):
    project_id = "on_exit_if_demo"
    db.ensure_project(project_id)
    db.save_project_files(project_id, {"index.yml": IF_INDEX.encode()}, {"index.yml": "text/yaml"})
    db.publish_project(project_id)
    revision = db.get_project_revision(project_id)

    module_name = module_name_for(project_id)
    built = compile_contents({"index.yml": IF_INDEX}, module_name, tmp_path, revision)
    built.rename(package_dir(tmp_path, module_name, revision))

    automaton = CompiledAutomatonLoader(db, tmp_path).load_at_revision(project_id, revision)
    action = automaton.states["start"].actions[0]
    outcomes = [
        automaton.eval_action_on_exit(action, EvaluationScope({"env": {"counter": counter}}, automaton=automaton, state_key="start"))
        for counter in (5, 1)
    ]

    assert outcomes == [({"counter": 50}, None, ()), ({"counter": 0}, None, ())]
