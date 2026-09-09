"""`project-service.compiled-automaton` — read by the platform package,
not by AppConfig.

It used to name a module, then became an on/off switch, and it now lives
where the choice it makes lives: a backend without src/avance_platform/
has no compiled-or-interpreted decision to take and does not read the
setting at all (see avance_platform/config.py).
"""
from __future__ import annotations

from pathlib import Path

import pytest

from avance_platform import config as platform_config

pytestmark = pytest.mark.contract

PATH = Path("config.yml")


def test_absent_or_off_means_the_interpreted_loader():
    assert platform_config.serves_compiled({}, PATH) is False
    assert platform_config.serves_compiled({"project-service": {}}, PATH) is False
    assert platform_config.serves_compiled({"project-service": {"compiled-automaton": False}}, PATH) is False


def test_on_means_the_compiled_loader():
    assert platform_config.serves_compiled({"project-service": {"compiled-automaton": True}}, PATH) is True


def test_a_module_name_is_a_configuration_error():
    """The old string form must be refused rather than accepted and
    quietly ignored — which package answers for which project and
    revision is decided per load, never here."""
    with pytest.raises(ValueError, match="true or false"):
        platform_config.serves_compiled({"project-service": {"compiled-automaton": "vueling_refund"}}, PATH)
