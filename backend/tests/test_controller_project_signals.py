"""GET /api/projects/{name}/signals — see ProjectService.get_project_signals.
Each signal's own `relevant` field (see Automaton.all_triggerable_signal_names)
is what the Inspector Signals tab's "show only relevant signals" filter reads
directly, rather than re-deriving it client-side from raw trigger text.
"""
from __future__ import annotations

import io
import zipfile

PROJECT = """
init-action:
  target: a

signals:
  progress:
    definition: "How far along the exercise the user is, 0-100."
  score:
    definition: "The user's own score for this exercise, 0-100."

states:
  a:
    contextual-prompt: "hi"
    actions:
      - name: advance
        ui-label: Advance
        target: b
        trigger: "progress == 100"
  b:
    contextual-prompt: "bye"
"""


def _zip_of(yaml_text: str) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("index.yml", yaml_text)
    return buffer.getvalue()


def _upload(client, name: str, yaml_text: str):
    response = client.put(
        f"/api/projects/{name}", content=_zip_of(yaml_text), headers={"Content-Type": "application/zip"}
    )
    assert response.status_code == 200, response.text


def test_signals_report_whether_a_trigger_references_them(client):
    _upload(client, "relevance-test", PROJECT)

    response = client.get("/api/projects/relevance-test/signals")

    assert response.status_code == 200
    by_name = {s["name"]: s for s in response.json()["signals"]}
    assert by_name["progress"]["relevant"] is True
    assert by_name["score"]["relevant"] is False


def test_a_signal_referenced_only_via_env_field_is_also_relevant(client):
    project = PROJECT.replace(
        'trigger: "progress == 100"',
        'trigger: "progress == 100"\n        env:\n          last_score: score',
    )
    _upload(client, "relevance-env-test", project)

    response = client.get("/api/projects/relevance-env-test/signals")

    by_name = {s["name"]: s for s in response.json()["signals"]}
    assert by_name["score"]["relevant"] is True
