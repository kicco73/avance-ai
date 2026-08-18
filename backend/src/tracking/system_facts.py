"""The `system` namespace a trigger/`env:` expression resolves against
(see tracking.evaluation_scope.EvaluationScopeBuilder) — facts
independent of any particular user/session/project: just the current
moment. Every method is a zero-argument proxy (called as `system.
today()`, never read as a bare attribute) so a value is only ever
computed if an expression actually references it — see this class's own
docstring parity with SessionFacts, which needs the same shape but does
have real dependencies (db/username/project_name) this one doesn't.
"""
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
