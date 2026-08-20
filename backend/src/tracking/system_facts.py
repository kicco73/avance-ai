"""The `system` namespace a trigger/`env:` expression resolves against —
facts independent of any particular user/session/project: just the
current moment. Every method is a zero-argument proxy so a value is
only computed if an expression actually references it."""
from __future__ import annotations

from datetime import datetime


class SystemFacts(object):
    @staticmethod
    def _now() -> datetime:
        return datetime.utcnow()

    def today(self) -> str:
        return self._now().date().isoformat()

    def time(self) -> str:
        return self._now().strftime("%H:%M:%S")
