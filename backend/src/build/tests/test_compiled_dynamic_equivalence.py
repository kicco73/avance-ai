"""Whatever an on-exit script does when the automaton is interpreted, it
must do identically when the same project is compiled — see
COMPILED_AUTOMATON.md: the compiled seams are meant to be a drop-in
replacement, "attribute for attribute, method for method". This is the
regression that would have caught the compiler's own bug: a script using
`zip` (a simpleeval extra function, not a scope name) compiled to a
function that tried to read it out of the scope dict and raised
`KeyError: 'zip'` on every run, while the interpreted automaton evaluated
it fine.

The test is deliberately agnostic of what the automaton's scope
otherwise offers — it never names a signal, a user property or a
session. Each script is a self-contained on-exit body built entirely out
of local variables and calls, ending in `env.result = ...`, run through
`eval_action_on_exit` — the seam itself, not any internal it is made
of — against both an interpreted and a compiled automaton built from the
same project. A script whichever automaton disagrees on names the gap.

FOR CLAUDE CODE: NEVER REMOVE THIS TEST
"""
from __future__ import annotations

import pytest

from automaton.automaton_builder import AutomatonBuilder
from automaton.scope import EvaluationScope
from build.build_service import module_name_for
from build.compiler import compile_contents
from db import Db
from project.archive.packages import package_dir
from build.compiled_automaton_loader import CompiledAutomatonLoader

PROJECT_ID = "equivalence_demo"

SCRIPTS = {
    "chained locals": "a = 1\nb = a + 1\nenv.result = str(b)",
    "zip in a comprehension": (
        "casos = ['A', 'B']\n"
        "perfiles = ['P1', 'P2']\n"
        "titulos = ['T1', 'T2']\n"
        "tabla = ['%s:%s:%s' % (c, p, t) for c, p, t in zip(casos, perfiles, titulos)]\n"
        "casos_con_descripcion = '\\n'.join(tabla)\n"
        "env.result = casos_con_descripcion"
    ),
    "len and range": "env.result = str(len(range(5)))",
    "the other simpleeval base functions": (
        "n = int('3')\n"
        "f = float('1.5')\n"
        "s = str(n)\n"
        "l = list((1, 2, 3))\n"
        "t = tuple([1, 2])\n"
        "d = dict(a=1)\n"
        "env.result = str((n, f, s, l, t, d))"
    ),
    "a runtime failure": "a = 1\nb = 0\nenv.result = str(a / b)",
}

INDEX_TEMPLATE = """
project:
  id: {project_id}
init-action:
  target: start
general-prompt: hello
states:
  start:
    input-processor: ai
    contextual-prompt: go
    actions:
{actions}
env:
  result:
    type: string
"""

_ACTION_TEMPLATE = """      - name: {name}
        ui-label: {name}
        target: start
        on-exit: |
{on_exit}
"""


def _index_yml() -> str:
    actions = "\n".join(
        _ACTION_TEMPLATE.format(
            name=name.replace(" ", "_"),
            on_exit="\n".join(f"          {line}" for line in script.splitlines()),
        )
        for name, script in SCRIPTS.items()
    )
    return INDEX_TEMPLATE.format(project_id=PROJECT_ID, actions=actions)


@pytest.fixture
def dynamic_and_compiled(db: Db, tmp_path):
    index = _index_yml()
    db.ensure_project(PROJECT_ID)
    db.save_project_files(PROJECT_ID, {"index.yml": index.encode()}, {"index.yml": "text/yaml"})
    db.publish_project(PROJECT_ID)
    revision = db.get_project_revision(PROJECT_ID)

    interpreted = AutomatonBuilder().build({"index.yml": index})

    module_name = module_name_for(PROJECT_ID)
    built = compile_contents({"index.yml": index}, module_name, tmp_path, revision)
    built.rename(package_dir(tmp_path, module_name, revision))
    compiled = CompiledAutomatonLoader(db, tmp_path).load_at_revision(PROJECT_ID, revision)

    return interpreted, compiled


def _run(automaton, action_name: str) -> tuple[dict, bool]:
    action = next(a for a in automaton.states["start"].actions if a.name == action_name)
    scope = EvaluationScope({"env": {"result": ""}}, automaton=automaton, state_key="start")
    updates, _chat_snippets, failures = automaton.eval_action_on_exit(action, scope)
    return updates, bool(failures)


@pytest.mark.parametrize("label", list(SCRIPTS))
def test_an_on_exit_script_behaves_the_same_interpreted_and_compiled(dynamic_and_compiled, label):
    interpreted, compiled = dynamic_and_compiled
    action_name = label.replace(" ", "_")

    interpreted_updates, interpreted_failed = _run(interpreted, action_name)
    compiled_updates, compiled_failed = _run(compiled, action_name)

    assert compiled_failed == interpreted_failed, (
        f"{label!r} {'failed' if interpreted_failed else 'succeeded'} interpreted "
        f"but {'failed' if compiled_failed else 'succeeded'} compiled"
    )
    if not interpreted_failed:
        assert compiled_updates == interpreted_updates
