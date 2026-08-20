"""Cross-project availability (Prompt 7) — a project is available when
its own build succeeds and every project it references via automaton.*
(the same reverse index Prompt 6 built) is itself available. See
project.project_service.ProjectService.recompute_availability/
register_availability_cascade's own docstrings for the exact mechanism:
a project's own is_paused/paused_reason only ever changes when the
recomputed value actually differs from what's saved — the guard that
lets a cascade (see events.events.AvailabilityChanged) converge in one
pass, safely, even across a mutual dependency, with no cycle detection
of its own.
"""
from __future__ import annotations

import asyncio

import pytest

from automaton.automaton_builder import AutomatonBuilder
from events import AvailabilityChanged, publish, subscribe
from project.project_service import ProjectService

pytestmark = pytest.mark.contract

USERNAME = "user"

VALID_YML = """
init-action:
  target: a
states:
  a:
    ui-label: A
    contextual-prompt: hi
"""


def _yml_observing(project_name: str) -> str:
    return f"""
init-action:
  target: x
states:
  x:
    ui-label: X
    contextual-prompt: hi
    actions:
      - name: notice
        target: x
        trigger: "automaton.{project_name}.state == 'never'"
"""


def _publish_project(db, project_service: ProjectService, project_name: str, index_yml: str) -> None:
    """Same helper test_wakeup_service.py uses — a real save, through
    _finalize_project_update, so the reverse index *and* the initial
    availability recompute (see that method's own new call to
    recompute_availability) both actually run, same as a real save."""
    db.ensure_project(project_name)
    db.save_project_files(project_name, {"index.yml": index_yml.encode("utf-8")}, {"index.yml": "text/yaml"})
    db.publish_project(project_name)
    db.set_active_project_name(project_name, USERNAME)
    automaton = AutomatonBuilder().build({"index.yml": index_yml})

    async def commit(_automaton):
        pass

    asyncio.run(project_service._finalize_project_update(project_name, automaton, commit))


@pytest.fixture
def project_service(db) -> ProjectService:
    return ProjectService(db)


def test_a_valid_project_with_no_dependencies_is_available(db, project_service):
    _publish_project(db, project_service, "solo", VALID_YML)

    assert db.get_project_availability("solo") == (False, None)  # (is_paused, paused_reason)


def test_a_project_whose_own_saved_content_fails_to_build_is_paused(db, project_service):
    # Bypasses ProjectService's own save-time validation on purpose —
    # every real save path already rejects a broken build outright (see
    # _prepare_project_update's own docstring: "never writes anything"),
    # so the only way this project ever ends up saved-but-broken is a
    # write that skips that gate entirely, same as e.g. a future
    # validation-rule change finding old, previously-valid content
    # invalid now. recompute_availability must still degrade gracefully
    # rather than raising.
    db.ensure_project("broken")
    db.save_project_files("broken", {"index.yml": b"not: [valid, yaml: at all"}, {"index.yml": "text/yaml"})
    db.publish_project("broken")

    project_service.recompute_availability("broken")

    is_paused, reason = db.get_project_availability("broken")
    assert is_paused is True
    assert "Build failed" in reason


def test_a_project_depending_on_a_paused_one_becomes_paused_too(db, project_service):
    _publish_project(db, project_service, "dependency", VALID_YML)
    _publish_project(db, project_service, "dependent", _yml_observing("dependency"))
    assert db.get_project_availability("dependent") == (False, None)

    db.set_project_availability("dependency", is_paused=True, paused_reason="manually paused")
    project_service.recompute_availability("dependent")

    is_paused, reason = db.get_project_availability("dependent")
    assert is_paused is True
    assert "dependency" in reason


def test_recompute_does_not_publish_when_nothing_actually_changed(db, project_service):
    _publish_project(db, project_service, "solo", VALID_YML)
    received = []
    subscribe(AvailabilityChanged, received.append)

    project_service.recompute_availability("solo")  # already available, still available

    assert received == []


def test_recompute_publishes_availability_changed_when_it_flips(db, project_service):
    _publish_project(db, project_service, "dependency", VALID_YML)
    _publish_project(db, project_service, "dependent", _yml_observing("dependency"))
    received = []
    subscribe(AvailabilityChanged, received.append)

    db.set_project_availability("dependency", is_paused=True, paused_reason="manually paused")
    project_service.recompute_availability("dependent")

    assert received == [AvailabilityChanged(project_name="dependent", available=False)]


def test_pausing_a_project_cascades_through_a_dependency_chain(db, project_service):
    # a -> b -> c (a observes b, b observes c)
    _publish_project(db, project_service, "c", VALID_YML)
    _publish_project(db, project_service, "b", _yml_observing("c"))
    _publish_project(db, project_service, "a", _yml_observing("b"))
    project_service.register_availability_cascade()

    db.set_project_availability("c", is_paused=True, paused_reason="c's own build broke")
    publish(AvailabilityChanged(project_name="c", available=False))

    b_paused, b_reason = db.get_project_availability("b")
    a_paused, a_reason = db.get_project_availability("a")
    assert b_paused is True and "c" in b_reason
    assert a_paused is True and "b" in a_reason


def test_recovering_a_project_cascades_availability_back_through_the_chain(db, project_service):
    _publish_project(db, project_service, "c", VALID_YML)
    _publish_project(db, project_service, "b", _yml_observing("c"))
    _publish_project(db, project_service, "a", _yml_observing("b"))
    project_service.register_availability_cascade()
    db.set_project_availability("c", is_paused=True, paused_reason="broken")
    publish(AvailabilityChanged(project_name="c", available=False))
    assert db.get_project_availability("a")[0] is True
    assert db.get_project_availability("b")[0] is True

    # "c" itself gets fixed and re-saved — a real recompute (build
    # succeeds, no paused dependency of its own) rather than a manual
    # flip, closer to what a real recovery looks like.
    project_service.recompute_availability("c")
    publish(AvailabilityChanged(project_name="c", available=True))

    assert db.get_project_availability("c") == (False, None)
    assert db.get_project_availability("b") == (False, None)
    assert db.get_project_availability("a") == (False, None)


def test_a_mutual_dependency_between_two_projects_converges_without_looping_forever(db, project_service):
    # a observes b AND b observes a — a genuine cycle. This test's own
    # completion (no RecursionError/hang) is half the assertion; the
    # other half is that both ends up paused exactly once.
    _publish_project(db, project_service, "a", _yml_observing("b"))
    _publish_project(db, project_service, "b", _yml_observing("a"))
    project_service.register_availability_cascade()
    received = []
    subscribe(AvailabilityChanged, received.append)

    db.set_project_availability("a", is_paused=True, paused_reason="a's own build broke")
    publish(AvailabilityChanged(project_name="a", available=False))

    assert db.get_project_availability("a")[0] is True
    assert db.get_project_availability("b")[0] is True
    # "a"'s own event is this test's own direct publish() call; "b"'s is
    # the cascade reacting to it (nested inside that same publish() —
    # see dispatcher.publish's own docstring on handlers running
    # synchronously — which is why "b" is actually recorded *before*
    # "a" below: this test's own listener was subscribed after
    # ProjectService's cascade handler, so it only sees the outer "a"
    # event once every nested effect it caused, "b" included, already
    # ran). Convergence is the interesting part, not this ordering
    # detail: "b"'s own event cascades back to "a" (its own observer),
    # whose recompute finds it's *already* paused — the guard's
    # "unchanged, don't republish" — so exactly these two events fire,
    # total, never a third ("a" again) or a loop.
    assert set(received) == {
        AvailabilityChanged(project_name="a", available=False),
        AvailabilityChanged(project_name="b", available=False),
    }


def test_depending_on_a_project_that_does_not_exist_at_all_is_not_itself_blocking(db, project_service):
    """A dangling automaton.* reference is a *runtime* concern (see
    tracking.automaton_namespace's own 'project_not_found' SystemWarning)
    — not, on its own, a reason to pause the *referencing* project at
    build time: the referenced project might simply not exist *yet*."""
    _publish_project(db, project_service, "dependent", _yml_observing("nonexistent"))

    assert db.get_project_availability("dependent") == (False, None)
