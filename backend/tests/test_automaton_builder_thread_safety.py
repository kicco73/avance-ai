"""AutomatonBuilder parses from several threads at once — every sync
endpoint runs in Starlette's threadpool, and the SPA fetches every
project's metadata in parallel on load. A module-level ruamel YAML
instance is not thread-safe (its reader/scanner/parser live on the
instance for the duration of load()), and in production produced
ParserError/IndexError pointing at *another* project's lines.
"""
from __future__ import annotations

import threading

import pytest

from automaton.automaton_builder import AutomatonBuilder

pytestmark = pytest.mark.regression


def _index_yml(n: int) -> str:
    states = "\n".join(
        f"""  s{i}:
    ui-label: State {i}
    contextual-prompt: |
      Prompt for state {i} of project p{n}.
      Second line, long enough to give the scanner some work.
    actions:
      - name: a{i}
        ui-label: Action {i}
        target: s{(i + 1) % 8}
"""
        for i in range(8)
    )
    return f"project:\n  id: p{n}\ninit-action:\n  target: s0\nstates:\n{states}"


def test_concurrent_builds_never_interleave_documents():
    documents = [_index_yml(n) for n in range(6)]
    errors: list[BaseException] = []
    ids: list[str] = []
    lock = threading.Lock()

    def build_many(index: int) -> None:
        for _ in range(25):
            try:
                automaton = AutomatonBuilder().build({"index.yml": documents[index]})
            except BaseException as exc:  # noqa: BLE001 — collected, asserted below
                with lock:
                    errors.append(exc)
                return
            with lock:
                ids.append(automaton.project_id)

    threads = [threading.Thread(target=build_many, args=(i,)) for i in range(len(documents))]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert not errors, errors[:3]
    assert len(ids) == 25 * len(documents)
