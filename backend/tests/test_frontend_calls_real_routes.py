"""Every path the frontend asks for is a path the backend declares.

The contract between the two halves, and the one nothing else checks.
test_route_ownership.py reads the backend decorators, so it cannot see a
caller; the frontend's own skill-boundary test reads the first segment,
so it cannot see whether the rest of the path leads anywhere. A route
that moved and a call that did not fall between them.

That is not hypothetical. The migration to /api/skills/<key>/ matched
"/api/<domain>" in both halves, but the frontend writes its URLs as
`${API_URL}/<domain>` with API_URL already ending in /api — so
thirty-eight calls kept the old shape, sat on master returning 404, and
were invisible to a suite that mocks the fetch layer.

Paths are compared with every interpolation collapsed to a placeholder,
since a {project_id} on one side is `${encodeURIComponent(projectId)}`
on the other. That makes this a check of shape, not of a working
request: it catches a path nobody serves, not a wrong id.

Reads frontend/src, so a frontend change can turn this red. That is the
point of a contract test, and the failure names the file and the path.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parent.parent
FRONTEND_SRC = BACKEND_ROOT.parent / "frontend" / "src"
SRC_ROOT = BACKEND_ROOT / "src"

ROUTE_DECORATORS = {"get", "post", "put", "delete", "route"}
PLACEHOLDER = "{}"
# `${API_URL}` followed by the path, up to the end of the template
# literal or the start of a query string.
CALL = re.compile(r"\$\{API_URL\}(/[^`?\s]*)")
INTERPOLATION = re.compile(r"\$\{[^}]*\}")
PARAMETER = re.compile(r"\{[^}]*\}")
# A placeholder glued to the end of a segment rather than standing as
# one — `.../tests${query}` — is an appended query string, not a path
# parameter, which always follows a slash.
APPENDED_QUERY = re.compile(r"(?<=[^/])\{\}$")


def normalised(path: str) -> str:
    """One shape for both spellings: every interpolation or path
    parameter becomes the same placeholder, and neither a trailing slash
    nor an appended query string is a difference."""
    collapsed = PARAMETER.sub(PLACEHOLDER, INTERPOLATION.sub(PLACEHOLDER, path))
    return APPENDED_QUERY.sub("", collapsed).rstrip("/") or "/"


def backend_routes() -> set[tuple[str, ...]]:
    declared = set()
    for path in SRC_ROOT.rglob("*_controller.py"):
        declared |= {tuple(normalised(route).split("/")) for route in _decorated_paths(path)}
    return declared


def served_by(call: tuple[str, ...], declared: set[tuple[str, ...]]) -> bool:
    """A placeholder stands for some value, so it matches a literal: the
    editor addresses both .../files/index.yml/ai-edit and its index.css
    twin through one `${fileName}` builder, and those are two real routes
    rather than a call nobody serves."""
    return any(
        len(route) == len(call)
        and all(PLACEHOLDER in (ours, theirs) or ours == theirs for ours, theirs in zip(route, call))
        for route in declared
    )


def _decorated_paths(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return [
        route
        for node in ast.walk(tree)
        for decorator in getattr(node, "decorator_list", [])
        for route in _route_of(decorator)
    ]


def _route_of(decorator) -> list[str]:
    call = decorator if isinstance(decorator, ast.Call) else None
    name = getattr(getattr(call, "func", None), "id", None)
    args = [a.value for a in getattr(call, "args", []) if isinstance(a, ast.Constant)]
    wanted = args[1:] if name == "route" else args[:1]
    return wanted[:1] * (name in ROUTE_DECORATORS)


def frontend_calls() -> list[tuple[Path, str]]:
    return [
        (source.relative_to(FRONTEND_SRC), normalised(match))
        for source in sorted(FRONTEND_SRC.rglob("*.js"))
        for match in CALL.findall(source.read_text(encoding="utf-8"))
    ]


def test_every_frontend_call_reaches_a_declared_route():
    declared = backend_routes()
    # The API_URL the frontend holds already ends in /api, which is the
    # whole reason this check exists: the two halves write the same path
    # differently, and a sweep over one spelling misses the other.
    missing = [
        (source, "/api" + path)
        for source, path in frontend_calls()
        if not served_by(tuple(normalised("/api" + path).split("/")), declared)
    ]
    assert not missing, "frontend calls nothing serves:\n" + "\n".join(
        f"  {source} → {path}" for source, path in sorted(set(missing))
    )


def test_the_frontend_actually_was_scanned():
    """A regex that matches nothing passes the test above for free. This
    fails if the frontend stops being readable from here, or stops
    writing its URLs the way the pattern expects."""
    assert len(frontend_calls()) > 100
