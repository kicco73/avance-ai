"""Every field the frontend reads off a session is a field the backend
sends.

The other half of the contract. test_frontend_calls_real_routes.py
compares paths, so it sees a route that moved; nothing saw a *field* that
was renamed — and the frontend's own tests cannot, because every one of
them mocks the session payload by hand and the mocks were renamed along
with nothing.

That is not hypothetical either. `active` became `current` in
TurnService._session_payload; the frontend went on reading
`session.active`, got undefined, and disabled the chat input on every
session with "This session is no longer active." Three commits and a
full green suite on both sides.

Compared by name only, in one direction: a field the backend sends and
nobody reads is not a fault (a payload may serve more than one reader),
a field read and never sent always is. Reads frontend/src, so a frontend
change can turn this red — which is the point.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parent.parent
FRONTEND_SRC = BACKEND_ROOT.parent / "frontend" / "src"
PAYLOAD = BACKEND_ROOT / "src" / "turn" / "turn_service.py"

#: `session.<field>` and `session?.<field>`, in .js and .vue alike.
READ = re.compile(r"\bsession\??\.([a-z_][a-z0-9_]*)\b")

#: Fields a session payload carries from somewhere other than
#: _session_payload, each with the method that adds it. Listed rather
#: than discovered: a field nobody can point at is the thing this test is
#: for.
ADDED_ELSEWHERE = {
    "state": "_session_response",
    "unsupported_revision": "_list_sessions_by_type",
    "paused": "get_current_session_if_any_or_create_new (a refusal, not a session)",
    "paused_reason": "get_current_session_if_any_or_create_new",
    "legal_terms_pending": "_legal_terms_pending_response (a refusal, not a session)",
}

#: Reads on something else the code happens to call `session`. Each one
#: is a different object, not a session payload.
NOT_A_SESSION_PAYLOAD = {
    "metric",              # a metrics row, in a per-session loop
    "value",               # ditto
    "number_of_user_sessions",  # a project's own counter
}


def _sent_fields() -> set[str]:
    """The keys _session_payload builds, read off the source rather than
    by calling it: this test must not need a database."""
    tree = ast.parse(PAYLOAD.read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_session_payload":
            returned = next(n for n in ast.walk(node) if isinstance(n, ast.Dict))
            return {key.value for key in returned.keys if isinstance(key, ast.Constant)}
    raise AssertionError("_session_payload is gone from turn_service.py — this test is reading the wrong file.")


def _read_fields() -> dict[str, set[str]]:
    found: dict[str, set[str]] = {}
    for path in sorted(FRONTEND_SRC.rglob("*")):
        if path.suffix not in (".js", ".vue") or not path.is_file():
            continue
        for field in READ.findall(path.read_text()):
            found.setdefault(field, set()).add(str(path.relative_to(FRONTEND_SRC)))
    return found


def test_the_frontend_reads_no_session_field_the_backend_does_not_send():
    sent = _sent_fields() | set(ADDED_ELSEWHERE) | NOT_A_SESSION_PAYLOAD

    unknown = {
        field: sorted(files) for field, files in _read_fields().items() if field not in sent
    }

    assert unknown == {}, (
        "the frontend reads session fields the backend never sends: "
        + "; ".join(f"{field} ({', '.join(files)})" for field, files in sorted(unknown.items()))
    )


def test_the_payload_was_actually_read():
    """A _session_payload this could not find would pass the check above
    for free."""
    sent = _sent_fields()

    assert {"id", "channel", "current", "type"} <= sent, sent
    assert len(sent) > 10, sent


def test_every_field_listed_as_coming_from_elsewhere_really_does():
    """The allowlist above is a claim about the code, so it has to fail
    when the code stops agreeing — otherwise it becomes the place a
    renamed field goes to hide."""
    source = PAYLOAD.read_text()
    sent = _sent_fields()

    for field, method in ADDED_ELSEWHERE.items():
        assert field not in sent, f"{field} is in _session_payload now — drop it from ADDED_ELSEWHERE"
        assert f'"{field}"' in source, f"nothing in turn_service.py sends {field} any more (was {method})"
