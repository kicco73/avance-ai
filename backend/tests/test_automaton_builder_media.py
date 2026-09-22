"""`media.<doc_id>.url()` — an on-exit-only namespace, one attribute per
file uploaded under a project's own `media/` folder, `doc_id` its
basename without extension. Unlike attachment.read (a whole-file text
read, task/on-exit), it never touches the file's own bytes — it only
returns the same download url every other reader of a project's files
already serves off, for chat.show_media(url) to hand to the browser.
Every reference is validated at build time the same way source.<name>
is (see AutomatonValidator.validate_namespaced_expression, via
TriggerExpressionAnalyzer.media_refs)."""
from __future__ import annotations

import pytest

from automaton.automaton_builder import AutomatonBuilder

pytestmark = pytest.mark.contract


def _project(action_yaml: str) -> str:
    return f"""
project:
  id: p
init-action:
  target: a
states:
  a:
    input-processor: ai
    contextual-prompt: hi
    actions:
      - name: go
        target: b
{action_yaml}
  b:
    input-processor: ai
    contextual-prompt: there
"""


def _build(action_yaml: str, archives: dict | None = None):
    return AutomatonBuilder().build({"index.yml": _project(action_yaml), **(archives or {})})


def test_media_url_is_readable_from_on_exit_inside_a_bare_chat_call():
    automaton = _build(
        "        on-exit: chat.show_media(media.report.url())\n",
        {"media/report.pdf": b"%PDF fake"},
    )
    assert automaton.states["a"].actions[0].on_exit == "chat.show_media(media.report.url())"


def test_media_url_is_readable_from_an_env_or_local_assignment_too():
    automaton = _build(
        "        on-exit: |\n"
        "          link = media.report.url()\n"
        "          chat.notify('Ready', link)\n",
        {"media/report.pdf": b"%PDF fake"},
    )
    assert "media.report.url()" in automaton.states["a"].actions[0].on_exit


def test_an_undeclared_doc_id_is_rejected():
    with pytest.raises(ValueError, match=r"undefined name\(s\).*media\.nope"):
        _build(
            "        on-exit: chat.show_media(media.nope.url())\n",
            {"media/report.pdf": b"%PDF fake"},
        )


def test_a_method_other_than_url_is_rejected():
    with pytest.raises(ValueError, match=r"undefined name\(s\).*media\.report\.read"):
        _build(
            "        on-exit: chat.show_media(media.report.read())\n",
            {"media/report.pdf": b"%PDF fake"},
        )


def test_extra_arguments_to_url_are_rejected():
    with pytest.raises(ValueError, match=r"media\.report\.url\(\.\.\.\)"):
        _build(
            "        on-exit: chat.show_media(media.report.url('x'))\n",
            {"media/report.pdf": b"%PDF fake"},
        )


def test_doc_id_is_the_basename_without_extension_regardless_of_kind():
    for name, doc_id in (("media/report.pdf", "report"), ("media/photo.png", "photo"), ("media/notes.md", "notes")):
        automaton = _build(f"        on-exit: chat.show_media(media.{doc_id}.url())\n", {name: b"x"})
        assert automaton.states["a"].actions[0].on_exit == f"chat.show_media(media.{doc_id}.url())"


def test_a_basename_that_is_not_a_valid_identifier_is_simply_unreachable():
    with pytest.raises(ValueError, match=r"undefined name\(s\).*media\.my"):
        _build(
            "        on-exit: chat.show_media(media.my.url())\n",
            {"media/my file.pdf": b"x"},
        )


def test_media_is_not_available_from_a_trigger():
    with pytest.raises(ValueError, match=r"undefined name\(s\).*media\.report"):
        _build(
            "        trigger: \"media.report.url() != ''\"\n",
            {"media/report.pdf": b"%PDF fake"},
        )


def test_media_is_not_available_from_task():
    with pytest.raises(ValueError, match=r"undefined name\(s\).*media\.report"):
        _build(
            "        task: media.report.url()\n",
            {"media/report.pdf": b"%PDF fake"},
        )
