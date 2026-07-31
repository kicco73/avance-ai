from __future__ import annotations

import pytest

from db import Db


@pytest.fixture
def db() -> Db:
    """A fresh in-memory SQLite database per test — db.py's `database`
    Proxy is a module-level global, so each Db(...) call simply rebinds it
    to a brand new connection (fine for the sequential tests here)."""
    return Db("sqlite:///:memory:")
