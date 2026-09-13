"""What kind of backend this is, decided where the loader is settled.

Three shapes exist and nothing records which one a build produced: an
authoring backend reads its projects from the database, a compiling one
replaces the loader for itself, and a product has one package beside it
and no project of its own. The first two are observable — one has rows,
the other claims the point — so the third is what is left, and that is
the whole of the decision these pin.

It used to be a package of its own that a build had to be told to
include, and the Build view left it out by default, so the delivery that
cannot run without it was the one that shipped without it. Nothing
caught that, because that package had no tests.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from project.archive.loader_choice import AutomatonLoaderChoice

PROJECT_ID = "demo"


class FakeDb:
    """A database at the port the choice already takes, saying only the
    one thing it is asked: whether this backend has projects of its own."""

    def __init__(self, projects: list[str]) -> None:
        self._projects = projects

    def list_projects(self) -> list[str]:
        return list(self._projects)


class Default:
    """Stands in for the Db/Archive loader main.py always builds."""


class Claimed:
    """Stands in for a loader some package replaced it with."""


def package(apps_dir: Path, name: str, revision: int, project_id: str = PROJECT_ID) -> Path:
    """A compiled package as a delivery carries it: a directory named for
    its revision, declaring the revision it came from and the automaton it
    serves."""
    apps_dir.mkdir(parents=True, exist_ok=True)
    directory = apps_dir / f"{name}.{revision}"
    directory.mkdir()
    (directory / "__init__.py").write_text(
        "class _Automaton:\n"
        f"    project_id = {project_id!r}\n"
        "\n"
        f"STORAGE_REVISION = {revision}\n"
        "AUTOMATON = _Automaton()\n",
        encoding="utf-8",
    )
    return directory


def choice(tmp_path: Path, projects: list[str]) -> AutomatonLoaderChoice:
    return AutomatonLoaderChoice(
        db=FakeDb(projects),
        session_manager=None,
        apps_dir=tmp_path / "apps",
        loader=Default(),
    )


def test_a_contributor_that_claimed_the_loader_gets_it(tmp_path):
    settling = choice(tmp_path, projects=[])
    claimed = Claimed()

    settling.replace(claimed, "someone")

    assert settling.settled() is claimed


def test_a_claim_wins_even_where_a_package_would_otherwise_be_served(tmp_path):
    """A backend that can compile decides per project and per revision,
    which is finer than "there is one package here" — so a claim is never
    second-guessed by what happens to be on disk."""
    settling = choice(tmp_path, projects=[])
    package(settling.apps_dir, "demo_app", 3)
    claimed = Claimed()

    settling.replace(claimed, "someone")

    assert settling.settled() is claimed


def test_a_second_claim_is_refused_naming_both(tmp_path):
    settling = choice(tmp_path, projects=[])
    settling.replace(Claimed(), "first")

    with pytest.raises(RuntimeError) as refused:
        settling.replace(Claimed(), "second")

    assert "first" in str(refused.value)
    assert "second" in str(refused.value)


def test_a_backend_with_projects_of_its_own_reads_them_from_the_database(tmp_path):
    """Even with a package beside it: an authoring backend accumulates one
    per build it has made, and none of them is what it serves."""
    settling = choice(tmp_path, projects=[PROJECT_ID])
    package(settling.apps_dir, "demo_app", 3)

    assert isinstance(settling.settled(), Default)


def test_a_backend_with_no_projects_of_its_own_serves_the_package_beside_it(tmp_path):
    settling = choice(tmp_path, projects=[])
    package(settling.apps_dir, "demo_app", 3)

    settled = settling.settled()

    assert settled.project_id == PROJECT_ID
    assert settled.revision == 3


def test_the_package_is_served_without_anyone_asking_for_it(tmp_path):
    """The point of the change: no flag, no package to include, nothing to
    tick. Having nothing else to serve is the whole of the decision."""
    settling = choice(tmp_path, projects=[])
    package(settling.apps_dir, "demo_app", 3)

    settling.settled()

    assert settling.chosen_by == "package"


def test_a_backend_with_no_projects_and_no_package_still_answers(tmp_path):
    """A platform nobody has uploaded to yet. It has nothing to serve
    either way, and must not fail to start over it."""
    settling = choice(tmp_path, projects=[])

    assert isinstance(settling.settled(), Default)


def test_two_packages_are_not_a_product_so_the_database_stays_the_source(tmp_path):
    """A product carries exactly one. Two is not a rule to resolve, so
    nothing is resolved and the backend starts on what it always had."""
    settling = choice(tmp_path, projects=[])
    package(settling.apps_dir, "demo_app", 3)
    package(settling.apps_dir, "other_app", 1, project_id="other")

    assert isinstance(settling.settled(), Default)


def test_a_package_that_cannot_be_imported_does_not_stop_the_backend(tmp_path):
    settling = choice(tmp_path, projects=[])
    broken = package(settling.apps_dir, "demo_app", 3)
    (broken / "__init__.py").write_text("this is not python(", encoding="utf-8")

    assert isinstance(settling.settled(), Default)
