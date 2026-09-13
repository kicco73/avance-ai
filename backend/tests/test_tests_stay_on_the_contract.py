"""A test drives a public entry point and observes a public result.

The a-priori half of CLAUDE.md's "What a test may look at": reading the
suite's own AST, with nothing constructed. A test that calls a private
method or reads a private attribute fails on a rename and holds when the
product breaks — and one of them here could not fail at all. It patched
`tracking_service._metrics`, which the code path under test never uses,
so its assertion passed whether the gate worked, was inverted, or was
deleted.

This counted 240 reaches across 56 files when it was written. What is
left is EXEMPTED below, one entry per file, each with the reason it
cannot be expressed against the public surface. The list only ever
shrinks: a new reach fails here, and an exemption whose test stops
needing it fails here too, so the list cannot rot into a blanket.

Name mangling is not what holds this line. GeminiProvider already
mangles `__build_contents` and the tests reached in anyway, spelling it
`provider._GeminiProvider__build_contents  # type: ignore`. That buys a
deterrent and a visible diff; this test is the gate.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

from conftest import SRC_ROOT

pytestmark = pytest.mark.contract

TESTS_ROOT = Path(__file__).resolve().parent

#: Named test seams, not reach-ins: each is a reset the harness owns and
#: the process needs between tests (see conftest's autouse fixtures).
SEAMS = {"_reset_for_tests"}

#: file -> {member: why it cannot be public}. Every entry is a decision.
EXEMPT: dict[str, dict[str, str]] = {
    "src/auth/tests/test_auth_service.py": {
        "_providers": "a real GoogleAuthProvider needs a real client id; the fake replaces it",
    },
    "src/avance_platform/tests/test_controller_sessions.py": {
        "_session_locks": "needs a turn still in flight, and the app fixture's AI service never blocks",
    },
    "src/avance_platform/tests/test_project_health.py": {
        "_file": "the build error's own fields, not a service's internals",
        "_line": "the build error's own fields, not a service's internals",
    },
    "src/build/tests/test_requirements_pruning.py": {
        "_write_requirements": "the only public driver is copy_backend(), which copytrees the whole backend first",
    },
    "src/talk/tests/test_talk_answers_for_itself.py": {
        "_INSTALLATIONS": "the skill's install registry is how a build without talk is simulated",
        "_NoTalk": "the skill's install registry is how a build without talk is simulated",
    },
    "src/testing/tests/test_abort_all_jobs.py": {
        "_jobs_by_key": "no public writer; a deterministic done/in-flight pair needs one",
    },
    "src/testing/tests/test_all_signals_shared_observations.py": {
        "_observations_for_run": "built-once is a performance property; a build and a cache hit are identical to every caller",
    },
    "src/testing/tests/test_controller_users_aggregation.py": {
        "_resolve_or_construct_dependencies": "rebuilding the tree resolves to the same completed rows, so it leaves no trace to observe",
    },
    "src/testing/tests/test_jobs_status_reflects_live_state.py": {
        "_compute": "the abstract hook a job subclass implements, held open to observe a live status",
        "_submit": "the only door to a job parked mid-run, and jobs/job_queue.py is off-limits",
    },
    "src/testing/tests/test_resolve_session_run_race.py": {
        "_resolve_or_construct_session_run": "the deterministic interleaving is the subject; nothing public parks a caller inside it",
    },
    "src/webchat/tests/test_webchat_flow.py": {
        "_PROVIDER_CLASSES": "the talk skill's provider registry, how a fake voice is installed",
        "_connections": "nothing tells a connection its own id, and input.audio is not CLIENT_INJECTABLE",
    },
    "src/whatsapp/tests/test_whatsapp_channel.py": {
        "_conversations": "owned by another session's rewrite",
    },
    "src/whatsapp/tests/test_whatsapp_webhook.py": {
        "_client": "the httpx transport seam, the public surface being the Cloud API itself",
    },
    "tests/test_ai_service_modes.py": {
        "_auto_provider": "AiService exposes no accessor for its auto cascade",
    },
    "tests/test_button_streams.py": {
        "_requests": "no queue at all, not merely an empty one: a None key would be shared by every session-less sender",
    },
    "tests/test_button_translation.py": {
        "_button_labels_to_translate": "pins this filter to the public pressable_actions it duplicates and could drift from",
    },
    "tests/test_provider_event_loops.py": {
        "_async_clients": "an unpruned per-loop client dict leaks silently and has no other observable",
    },
    "tests/test_session_enter.py": {
        "_requests": "no queue at all, not merely an empty one: a None key would be shared by every session-less sender",
    },
    "tests/test_session_lifecycle_lock.py": {
        "_session_lifecycle_locks": "the lock is the subject; a caller must be pinned inside it or the test passes vacuously",
    },
    "tests/test_wakeup_service.py": {
        "_reevaluate_and_apply": "pre-existing; the public path needs the file-backed db fixture and a wait loop",
    },
    "tests/test_watchdog_timeout.py": {
        "_recorded_seconds": "its subject is conftest's own watchdog, so conftest is the code under test",
    },
}


def _test_files() -> list[Path]:
    return sorted(
        [p for p in SRC_ROOT.rglob("tests/test_*.py")] + [p for p in TESTS_ROOT.glob("test_*.py")]
    )


def _reaches(path: Path) -> set[str]:
    found = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if not isinstance(node, ast.Attribute):
            continue
        if not node.attr.startswith("_") or node.attr.startswith("__") or node.attr in SEAMS:
            continue
        owner = node.value
        # A test's own fakes are its business; only another object's
        # privates are a reach into the implementation.
        if isinstance(owner, ast.Name) and owner.id == "self":
            continue
        found.add(node.attr)
    return found


def _relative(path: Path) -> str:
    for root, prefix in ((SRC_ROOT, "src"), (TESTS_ROOT, "tests")):
        if root in path.parents or root == path.parent:
            return f"{prefix}/{path.relative_to(root).as_posix()}"
    return path.as_posix()


def test_no_test_reaches_into_an_implementation_that_is_not_exempted():
    offenders = {}
    for path in _test_files():
        name = _relative(path)
        unexplained = _reaches(path) - set(EXEMPT.get(name, {}))
        if unexplained:
            offenders[name] = sorted(unexplained)
    assert not offenders, (
        "These tests read or call another object's private members. Drive the public entry "
        "point and observe the public result instead (CLAUDE.md, 'What a test may look at'). "
        f"If one genuinely has no public form, add it to EXEMPT with its reason: {offenders}"
    )


def test_every_exemption_is_still_needed():
    stale = {}
    for name, reasons in EXEMPT.items():
        path = SRC_ROOT / name[len("src/"):] if name.startswith("src/") else TESTS_ROOT / name[len("tests/"):]
        if not path.exists():
            stale[name] = "file is gone"
            continue
        unused = sorted(set(reasons) - _reaches(path))
        if unused:
            stale[name] = unused
    assert not stale, (
        "These exemptions are no longer used by the test that needed them. Delete the entry — "
        f"the list is only allowed to shrink: {stale}"
    )
