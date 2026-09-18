"""project.archive.packages.discard_all_packages."""
from __future__ import annotations

import pytest

from project.archive.packages import STAGING_PREFIX, discard_all_packages

pytestmark = pytest.mark.regression


def test_every_package_and_staging_directory_is_removed(tmp_path):
    (tmp_path / "demo.0" / "data").mkdir(parents=True)
    (tmp_path / "other.3").mkdir()
    (tmp_path / f"{STAGING_PREFIX}demo.1").mkdir()
    (tmp_path / "not_a_dir.txt").write_text("leave me alone")

    removed = discard_all_packages(tmp_path)

    assert {path.name for path in removed} == {"demo.0", "other.3", f"{STAGING_PREFIX}demo.1"}
    assert [p.name for p in tmp_path.iterdir()] == ["not_a_dir.txt"]


def test_a_missing_apps_dir_is_a_clean_no_op(tmp_path):
    assert discard_all_packages(tmp_path / "does-not-exist") == []
