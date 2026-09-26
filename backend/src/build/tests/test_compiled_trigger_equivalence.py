from __future__ import annotations

import pytest

from automaton.automaton_builder import AutomatonBuilder
from build.build_service import module_name_for
from build.compiled_automaton_loader import CompiledAutomatonLoader
from build.compiler import compile_contents
from db import Db
from project.archive.packages import package_dir

pytestmark = pytest.mark.contract

PROJECT_ID = "trigger_equivalence"

TRIGGERS = {
    "or_flag": "(signal.a + signal.b) / 2 <= 20 or env.flag",
    "not_equal": "signal.a != 50",
    "negated": "not signal.a",
    "bare": "signal.a",
    "and_or": "(env.flag and signal.a > 10) or env.n == 3",
    "no_signals": "env.n == 3",
    "malformed": "env.n / 0 > 1",
}

SCOPES = [
    ({}, False, 0), ({}, True, 0), ({}, True, 3), ({"a": 10, "b": 20}, False, 0),
    ({"a": 50, "b": 50}, True, 0), ({"a": 0}, False, 3), ({"b": 5}, False, 1),
]


def _yml() -> str:
    states = "".join(
        f"  {key}:\n"
        "    input-processor: ai\n"
        "    contextual-prompt: hi\n"
        "    actions:\n"
        "      - name: fire\n"
        f"        target: {key}\n"
        f"        trigger: \"{trigger}\"\n"
        for key, trigger in TRIGGERS.items()
    )
    return (
        f"project:\n  id: {PROJECT_ID}\n"
        "init-action:\n  target: or_flag\n"
        "signals:\n"
        "  a:\n    definition: a\n"
        "  b:\n    definition: b\n"
        "env:\n"
        "  flag:\n    type: bool\n"
        "  n:\n    type: number\n"
        f"states:\n{states}"
    )


@pytest.fixture
def interpreted_and_compiled(db: Db, tmp_path):
    index = _yml()
    db.ensure_project(PROJECT_ID)
    db.save_project_files(PROJECT_ID, {"index.yml": index.encode()}, {"index.yml": "text/yaml"})
    db.publish_project(PROJECT_ID)
    revision = db.get_project_revision(PROJECT_ID)
    module_name = module_name_for(PROJECT_ID)
    built = compile_contents({"index.yml": index}, module_name, tmp_path, revision)
    built.rename(package_dir(tmp_path, module_name, revision))
    compiled = CompiledAutomatonLoader(db, tmp_path).load_at_revision(PROJECT_ID, revision)
    return AutomatonBuilder().build({"index.yml": index}), compiled


def _fired(automaton, state_key: str, signals: dict, flag: bool, n: int) -> bool:
    scope = {"signal": {"a": None, "b": None, **signals}, "env": {"flag": flag, "n": n}}
    return automaton.evaluate_triggers_action(state_key, scope) is not None


@pytest.mark.parametrize("state_key", list(TRIGGERS))
def test_a_trigger_fires_the_same_interpreted_and_compiled_whatever_signals_are_missing(interpreted_and_compiled, state_key):
    interpreted, compiled = interpreted_and_compiled

    for signals, flag, n in SCOPES:
        assert _fired(compiled, state_key, signals, flag, n) == _fired(interpreted, state_key, signals, flag, n), (
            state_key, signals, flag, n,
        )
