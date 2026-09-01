"""The `source` namespace a trigger/`env:` expression resolves against —
"data sources", code-defined "plugins" an action's own `env:` field can
pull external/project data through, e.g. `news: source.rss('https://...')`.
Each source is its own module in this package (attachment.py is the
first); SourceNamespace below is just the fixed `source.<name>(...)`
dispatch table those modules are wired into — adding a source means
adding a module plus one method here delegating to it, never touching
tracking.evaluation_scope itself."""
from __future__ import annotations

from automaton.automaton import Automaton
from db import Db

from . import attachment as attachment_source


class SourceNamespace(object):
    def __init__(self, db: Db, automaton: Automaton) -> None:
        self._db = db
        self._automaton = automaton

    def attachment(self, name: str) -> str:
        return attachment_source.read(self._db, self._automaton, name)
