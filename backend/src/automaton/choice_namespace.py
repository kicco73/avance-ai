from __future__ import annotations

import ast
from typing import TYPE_CHECKING

from automaton.choice import ChoiceSelection
from automaton.trigger_expression_analyzer import TriggerExpressionAnalyzer
from automaton.trigger_namespaces import TriggerNamespace

if TYPE_CHECKING:
    from automaton.automaton import Action, Automaton, EnvKey, State

NAME = "choice"
LIST_TYPE = "list"


def chains(tree: ast.AST) -> list[tuple[str, ...]]:
    nested = {id(node.value) for node in ast.walk(tree) if isinstance(node, ast.Attribute)}
    called = {id(node.func) for node in ast.walk(tree) if isinstance(node, ast.Call)}
    found: list[tuple[tuple[int, int], tuple[str, ...]]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Attribute) or id(node) in nested:
            continue
        attrs = [node.attr]
        cur = node.value
        while isinstance(cur, ast.Attribute):
            attrs.append(cur.attr)
            cur = cur.value
        if isinstance(cur, ast.Name) and cur.id == NAME:
            chain = (cur.id, *reversed(attrs)) + (("()",) if id(node) in called else ())
            found.append(((cur.lineno, cur.col_offset), chain))
    return [chain for _position, chain in sorted(found, key=lambda item: item[0])]


def expression_chains(expression: str) -> list[tuple[str, ...]]:
    return chains(ast.parse(expression, mode="eval"))


def script_chains(script: str) -> list[tuple[str, ...]]:
    found: list[tuple[str, ...]] = []
    for _line_number, statement in TriggerExpressionAnalyzer.task_statements(script):
        found.extend(chains(ast.parse(statement, mode="exec")))
    return found


def list_key_names(env_keys: "dict[str, EnvKey] | list[EnvKey]") -> set[str]:
    keys = env_keys.values() if isinstance(env_keys, dict) else env_keys
    return {env_key.name for env_key in keys if env_key.type == LIST_TYPE}


class ChoiceNamespace(TriggerNamespace):
    name = NAME

    def check_action(self, state: "State", action: "Action", env_keys: "dict[str, EnvKey]") -> None:
        context = f"State {state.key}, action '{action.name}'"
        declared = list_key_names(env_keys)
        readable = [("trigger", action.trigger, expression_chains), ("on-exit", action.on_exit, script_chains)]
        for field_name, source, parser in readable:
            for chain in self._chains_of(source, parser):
                self._check_chain(context, field_name, chain, declared)
        for chain in self._chains_of(action.task, script_chains):
            raise ValueError(
                f"{context}: task references {'.'.join(chain)} — "
                f"{NAME}.* is read in an action's trigger and on-exit only."
            )

    @staticmethod
    def _chains_of(source: str | None, parser) -> list[tuple[str, ...]]:
        if not source:
            return []
        try:
            return parser(source)
        except SyntaxError:
            return []

    @staticmethod
    def _check_chain(context: str, field_name: str, chain: tuple[str, ...], declared: set[str]) -> None:
        if chain[-1] == "()":
            raise ValueError(
                f"{context}: {field_name} calls {'.'.join(chain[:-1])}() — "
                f"{NAME}.<key> is the option pressed, a string, not a call: compare it, e.g. "
                f"{NAME}.{chain[1]} != ''."
            )
        if len(chain) != 2:
            raise ValueError(
                f"{context}: {field_name} references {'.'.join(chain)} — {NAME}.<key> is the whole of it, "
                "with <key> an env key declared of type list."
            )
        if chain[1] not in declared:
            raise ValueError(
                f"{context}: {field_name} references {'.'.join(chain)} — '{chain[1]}' is not an env key "
                f"declared of type {LIST_TYPE}."
            )

    def scope_for(self, automaton: "Automaton", selection: ChoiceSelection) -> object:
        return _ChoiceScope(list_key_names(automaton.env_keys), selection)

    def identifiers(self, automaton: "Automaton") -> dict[str, dict[str, str]]:
        return {NAME: {
            env_key.name: env_key.ai_definition or "" for env_key in automaton.env_keys if env_key.type == LIST_TYPE
        }}


class _ChoiceScope:
    def __init__(self, declared: set[str], selection: ChoiceSelection) -> None:
        self._declared = declared
        self._selection = selection

    def __getattr__(self, key: str) -> str:
        if key not in self._declared:
            raise AttributeError(f"{NAME}.{key}: not an env key declared of type {LIST_TYPE}.")
        return self._selection.option if key == self._selection.key else ""
