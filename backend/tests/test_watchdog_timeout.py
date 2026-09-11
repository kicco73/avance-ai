"""A test that stops making progress must say where it stopped, and how
long it is given comes from how long it has taken before — see
conftest.watchdog_seconds and docs/TESTS.md.
"""
from __future__ import annotations

import ast

import pytest

import conftest
from conftest import BACKEND_DIR

pytestmark = pytest.mark.contract


@pytest.fixture
def recorded(monkeypatch):
    def _record(averages: dict[str, float]) -> None:
        monkeypatch.setattr(conftest, "_known_seconds", averages)
    return _record


def test_a_test_nobody_has_timed_yet_gets_the_unknown_allowance(recorded):
    recorded({})

    assert conftest.watchdog_seconds("tests/test_new.py::test_never_run") == conftest.WATCHDOG_UNKNOWN_SECONDS


def test_a_quick_test_is_not_given_ten_minutes_to_hang_in(recorded):
    recorded({"tests/test_quick.py::test_quick": 0.03})

    allowance = conftest.watchdog_seconds("tests/test_quick.py::test_quick")

    assert allowance == conftest.WATCHDOG_FLOOR_SECONDS
    assert allowance < conftest.WATCHDOG_UNKNOWN_SECONDS


def test_a_slow_test_is_given_room_in_proportion_to_what_it_takes(recorded):
    recorded({"tests/test_slow.py::test_slow": 5.0})

    assert conftest.watchdog_seconds("tests/test_slow.py::test_slow") == 5.0 * conftest.WATCHDOG_FACTOR


def test_no_test_is_given_more_than_the_ceiling(recorded):
    recorded({"tests/test_build.py::test_builds_a_backend": 254.0})

    assert conftest.watchdog_seconds("tests/test_build.py::test_builds_a_backend") == conftest.WATCHDOG_CEILING_SECONDS


def test_the_allowance_is_read_off_the_recorded_stats_file(monkeypatch, tmp_path):
    stats = tmp_path / "test_stats.json"
    stats.write_text('{"tests/test_x.py::test_x": {"seconds": 20.0, "runs": 4}}')
    monkeypatch.setattr(conftest, "TEST_STATS_PATH", stats)
    monkeypatch.setattr(conftest, "_known_seconds", None)

    assert conftest.watchdog_seconds("tests/test_x.py::test_x") == 5.0 * conftest.WATCHDOG_FACTOR


def test_a_missing_stats_file_leaves_every_test_with_the_unknown_allowance(monkeypatch, tmp_path):
    monkeypatch.setattr(conftest, "TEST_STATS_PATH", tmp_path / "gone.json")
    monkeypatch.setattr(conftest, "_known_seconds", None)

    assert conftest.watchdog_seconds("tests/test_x.py::test_x") == conftest.WATCHDOG_UNKNOWN_SECONDS


_TURN_CALLS = {"chat_turn", "chat_turn_frames", "chat_turn_error"}


def _waits_for_a_turn(node: ast.FunctionDef) -> bool:
    return any(
        isinstance(call, ast.Call)
        and isinstance(call.func, ast.Name)
        and call.func.id in _TURN_CALLS
        for call in ast.walk(node)
    )


def _tests_in(node, prefix: str):
    """Every test function under `node`, node-id style — a walk rather
    than a line-by-line scan, so a test that lives inside a class is
    found too, and found under the name pytest gives it."""
    for child in node.body:
        if isinstance(child, ast.ClassDef):
            yield from _tests_in(child, f"{prefix}::{child.name}")
        elif isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)) and child.name.startswith("test_"):
            yield f"{prefix}::{child.name}", child


def _turn_waiting_nodeids() -> list[str]:
    """Every test that blocks on a turn arriving over the websocket, and
    is therefore guarded by both nets at once."""
    files = sorted(BACKEND_DIR.glob("tests/test_*.py")) + sorted(BACKEND_DIR.glob("src/*/tests/test_*.py"))
    return sorted(
        nodeid
        for path in files
        for nodeid, node in _tests_in(ast.parse(path.read_text()), str(path.relative_to(BACKEND_DIR)))
        if _waits_for_a_turn(node)
    )


def test_the_net_that_knows_what_it_waits_for_always_fires_first():
    """Two nets guard a hung turn, and only the inner one can say which
    frames arrived — the outer one kills the process. Asked of the two
    functions rather than of the share they are built on: a share below
    one makes this true by algebra, but only until somebody puts a floor
    or a fixed number back into the frame deadline."""
    guarded = [
        nodeid for nodeid in _turn_waiting_nodeids()
        if nodeid in conftest._recorded_seconds()
    ]
    if not guarded:
        pytest.skip("no recorded durations for the tests that wait on a turn")

    late = [
        (nodeid, conftest.turn_frame_seconds(nodeid), conftest.watchdog_seconds(nodeid))
        for nodeid in guarded
        if conftest.turn_frame_seconds(nodeid) >= conftest.watchdog_seconds(nodeid)
    ]

    assert late == [], (
        "these tests would be killed before the diagnosis naming their frames is printed: "
        + ", ".join(f"{nodeid} ({deadline:.1f}s vs {allowance:.1f}s)" for nodeid, deadline, allowance in late)
    )


@pytest.mark.parametrize("average", [0.001, 0.03, 1.0, 60.0, 1000.0])
def test_the_frame_deadline_stays_under_the_allowance_at_every_size(recorded, average):
    """Including where the allowance stops following the duration: at the
    floor a quick test is clamped up, at the ceiling a slow one is clamped
    down, and either clamp is where a deadline computed apart would drift
    past it."""
    recorded({"tests/test_x.py::test_x": average})

    assert conftest.turn_frame_seconds("tests/test_x.py::test_x") < conftest.watchdog_seconds("tests/test_x.py::test_x")


def test_a_test_that_waits_for_a_turn_inside_a_class_is_found_too():
    """The scan used to read `def test_...` line by line, which missed
    every test living in a class — and would have gone on missing them
    silently, since nothing in this repo waits for a turn from inside one
    yet."""
    source = ast.parse(
        "def test_plain():\n"
        "    chat_turn(client, 1)\n"
        "class TestGroup:\n"
        "    def test_inside(self):\n"
        "        frames = chat_turn_frames(client, 1, 'hi')\n"
        "    def test_quiet(self):\n"
        "        assert True\n"
    )

    found = {nodeid: node for nodeid, node in _tests_in(source, "tests/test_x.py")}

    assert set(found) == {
        "tests/test_x.py::test_plain",
        "tests/test_x.py::TestGroup::test_inside",
        "tests/test_x.py::TestGroup::test_quiet",
    }
    assert _waits_for_a_turn(found["tests/test_x.py::TestGroup::test_inside"])
    assert not _waits_for_a_turn(found["tests/test_x.py::TestGroup::test_quiet"])
