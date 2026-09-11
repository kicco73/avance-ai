"""The frontend half of a build drops exactly the skills the backend half
drops — by key, not by package name, and by deleting the directory rather
than recording the choice anywhere.
"""
from __future__ import annotations

import pytest

from build.frontend_copy import FrontendBuildError, FrontendCopy

pytestmark = pytest.mark.contract


def _frontend_tree(root):
    (root / "src" / "skills" / "build").mkdir(parents=True)
    (root / "src" / "skills" / "talk").mkdir(parents=True)
    (root / "src" / "skills" / "platform").mkdir(parents=True)
    (root / "src" / "skills" / "registry.js").write_text("glob")
    (root / "src" / "skills" / "build" / "index.js").write_text("export const key = 'build'")
    (root / "src" / "skills" / "talk" / "index.js").write_text("export const key = 'talk'")
    (root / "node_modules" / "left-pad").mkdir(parents=True)
    (root / "dist").mkdir()
    (root / "dist" / "stale.js").write_text("old")
    (root / "package.json").write_text("{}")
    return root


def test_the_copy_leaves_out_what_a_frontend_never_needs(tmp_path):
    source = _frontend_tree(tmp_path / "frontend")
    copy = FrontendCopy(source, tmp_path / "out", [])

    copy._copy()

    assert (tmp_path / "out" / "package.json").is_file()
    assert not (tmp_path / "out" / "node_modules").exists()
    assert not (tmp_path / "out" / "dist").exists()


def test_an_excluded_package_takes_its_frontend_directory_with_it(tmp_path):
    source = _frontend_tree(tmp_path / "frontend")
    copy = FrontendCopy(source, tmp_path / "out", ["talk"])
    copy._copy()

    dropped = copy._prune_skills()

    assert dropped == {"talk"}
    assert not (copy.skills_dir / "talk").exists()
    assert (copy.skills_dir / "build").is_dir()
    assert (copy.skills_dir / "registry.js").is_file()


def test_the_directory_is_named_by_key_where_the_package_is_named_otherwise(tmp_path):
    """`excluded_skills` names packages; the frontend names directories
    after keys. avance_platform/platform is the pair that tells them apart."""
    source = _frontend_tree(tmp_path / "frontend")
    copy = FrontendCopy(source, tmp_path / "out", ["avance_platform"])
    copy._copy()

    dropped = copy._prune_skills()

    assert dropped == {"platform"}
    assert not (copy.skills_dir / "platform").exists()


def test_a_package_with_no_frontend_of_its_own_drops_nothing(tmp_path):
    source = _frontend_tree(tmp_path / "frontend")
    copy = FrontendCopy(source, tmp_path / "out", ["mail"])
    copy._copy()

    assert copy._prune_skills() == set()
    assert sorted(path.name for path in copy.skills_dir.iterdir()) == ["build", "platform", "registry.js", "talk"]


def test_a_delivered_source_still_naming_a_dropped_skill_fails_the_build(tmp_path):
    source = _frontend_tree(tmp_path / "frontend")
    (source / "src" / "leftover.js").write_text("import x from './skills/talk/api.js'")
    copy = FrontendCopy(source, tmp_path / "out", ["talk"])

    with pytest.raises(FrontendBuildError, match="still names talk"):
        copy.build()


def test_a_clean_copy_delivers_the_source_of_what_is_left(tmp_path):
    source = _frontend_tree(tmp_path / "frontend")
    copy = FrontendCopy(source, tmp_path / "out", ["talk"])

    report = copy.build()

    assert report["dropped_skills"] == ["talk"]
    assert report["path"] == str(tmp_path / "out")
    assert (copy.skills_dir / "build" / "index.js").is_file()


@pytest.mark.parametrize("dropped", ["build", "testing", "talk", "listen", "whatsapp"])
def test_the_real_frontend_delivered_without_one_skill_keeps_every_other(tmp_path, dropped):
    """The delivery proof, on the repo's own frontend rather than a
    fixture: drop one skill and what is left must still be a whole
    frontend that names it nowhere."""
    from build.backend_copy import REPO_ROOT

    expected = sorted({"build", "testing", "talk", "listen", "whatsapp"} - {dropped})

    report = FrontendCopy(REPO_ROOT / "frontend", tmp_path / "frontend", [dropped]).build()

    copy = FrontendCopy(REPO_ROOT / "frontend", tmp_path / "frontend", [dropped])
    assert report["dropped_skills"] == [dropped]
    assert not (copy.skills_dir / dropped).exists()
    assert (copy.skills_dir / "registry.js").is_file()
    assert sorted(path.name for path in copy.skills_dir.iterdir() if path.is_dir()) == expected
    assert not (tmp_path / "frontend" / "node_modules").exists()
