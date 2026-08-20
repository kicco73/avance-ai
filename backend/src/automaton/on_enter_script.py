"""Build-time validation for an action's own "on-enter" field (see
Action.on_enter) — a free-form client-side script (see the frontend's
onEnterActions.js), but not truly free-form: exactly one call per line
(blank lines allowed), each naming one of a fixed, known set of local
functions with the right number of positional arguments. This is a
*minimal* check — arity only, never argument content/types (a string
literal vs. some other expression) — the frontend's own sandboxed
runOnEnterScript is still what actually runs it; this only catches an
obviously-wrong script (a typo'd function name, a bare identifier with
no call at all, the wrong number of arguments) at save time instead of
it silently failing at runtime in the browser.
"""
from __future__ import annotations

import ast


class OnEnterScriptError(ValueError):
    """Raised by OnEnterScriptSignatureParser.validate — always carries a
    human-readable explanation of exactly what's wrong, line number
    included, since this is the message a project author sees when a
    save gets rejected because of it."""


# name -> expected positional argument count — kept in sync by hand with
# the frontend's own onEnterActions.js (its onEnterLocals) and
# OnEnterEditor.vue's own hardwired autocomplete; no runtime introspection
# ties the two together (backend Python and frontend JavaScript are
# separate languages), same as every other hand-kept-in-sync list in this
# codebase (see identifier_registry.py's own SYSTEM/SESSION docstring).
KNOWN_ON_ENTER_FUNCTIONS: dict[str, int] = {
    "celebrate": 0,
    "notify": 2,
}


class OnEnterScriptSignatureParser:
    """Parses/validates one on-enter script against the strict grammar:
    one function call per line (blank/whitespace-only lines are skipped),
    each call naming one of `known_functions` with exactly that many
    positional arguments — no keyword arguments, no starred/double-
    starred arguments, no attribute/subscript access, no nested calls, no
    non-call expression, no multiple statements on one line (a bare
    Python SyntaxError in eval mode already rules that out). A standalone
    class, not a method on AutomatonBuilder, so it can be unit-tested and
    reused on its own — see automaton_builder.py's own _build_action/
    _build_init_action, its only callers today."""

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
