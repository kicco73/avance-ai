"""Build-time validation for an action's "on-enter" script: exactly one
call per line, each naming a known local function with the right
positional argument count. Arity-only check — catches an obviously
wrong script at save time; the frontend's sandboxed runner still executes it.
"""
from __future__ import annotations

import ast


class OnEnterScriptError(ValueError):
    """Raised by OnEnterScriptSignatureParser.validate — the message a
    project author sees when a save gets rejected."""


# name -> expected positional argument count — kept in sync by hand with
# the frontend's onEnterActions.js and OnEnterEditor.vue's autocomplete;
# no runtime introspection ties the two languages together.
KNOWN_ON_ENTER_FUNCTIONS: dict[str, int] = {
    "celebrate": 0,
    "notify": 2,
}


class OnEnterScriptSignatureParser:
    """Parses/validates one on-enter script: one function call per line,
    each naming one of `known_functions` with exactly that many
    positional arguments — no kwargs, starred args, or nested calls."""

    def __init__(self, known_functions: dict[str, int] | None = None) -> None:
        self._known_functions = known_functions if known_functions is not None else KNOWN_ON_ENTER_FUNCTIONS

    def validate(self, script: str | None) -> None:
        """Raises OnEnterScriptError on the first line that doesn't parse
        as a known call. Does nothing for None/empty (on-enter is always
        optional)."""
        if not script:
            return
        for line_number, line in enumerate(script.splitlines(), start=1):
            if not line.strip():
                continue
            self._validate_line(line.strip(), line_number)

    def _validate_line(self, line: str, line_number: int) -> None:
        try:
            tree = ast.parse(line, mode="eval")
        except SyntaxError as exc:
            raise OnEnterScriptError(f"line {line_number}: not a valid function call — {exc.msg}.") from exc

        call = tree.body
        if not isinstance(call, ast.Call):
            raise OnEnterScriptError(
                f"line {line_number}: expected a single function call (e.g. \"celebrate()\"), "
                f"got a {type(call).__name__.lower()} instead."
            )
        if not isinstance(call.func, ast.Name):
            raise OnEnterScriptError(f"line {line_number}: expected a plain function name, not an attribute/subscript.")

        name = call.func.id
        if name not in self._known_functions:
            known = ", ".join(sorted(self._known_functions))
            raise OnEnterScriptError(f"line {line_number}: unknown function '{name}' — expected one of: {known}.")
        if call.keywords:
            raise OnEnterScriptError(f"line {line_number}: '{name}()' doesn't accept keyword arguments.")
        if any(isinstance(arg, ast.Starred) for arg in call.args):
            raise OnEnterScriptError(f"line {line_number}: '{name}()' doesn't accept a starred argument.")

        expected = self._known_functions[name]
        actual = len(call.args)
        if actual != expected:
            plural = "" if expected == 1 else "s"
            raise OnEnterScriptError(f"line {line_number}: '{name}()' takes {expected} argument{plural}, got {actual}.")
