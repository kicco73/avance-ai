from __future__ import annotations

from automaton.automaton_builder import AutomatonBuilder
from automaton.scope import EvaluationScope
from build.build_service import module_name_for
from build.compiled_automaton_loader import CompiledAutomatonLoader
from build.compiler import compile_contents
from db import Db
from project.archive.packages import package_dir
from tracking.actuators import AttachmentNamespace, FakeChatNamespace
from tracking.project_files import project_files_for

PROJECT_ID = "render_demo"
INDEX = """
project:
  id: render_demo
init-action:
  target: start
general-prompt: hello
states:
  start:
    input-processor: ai
    contextual-prompt: go
    actions:
      - name: go
        ui-label: Go
        target: start
        on-exit: |
          who = 'Ana'
          chat.show(attachment.report.render())
env:
  mood:
    type: string
"""
REPORT = "# {{ who }}\n\nMood: {{ env.mood }} — 100% {x} nombre_usuario"
FILES = {"index.yml": INDEX, "behaviour/report.md": REPORT}


def _shown(db: Db, automaton) -> tuple[str | None, tuple]:
    scope = EvaluationScope(
        {
            "env": {"mood": "happy"},
            "attachment": AttachmentNamespace(project_files_for(db, automaton), automaton),
            "chat": FakeChatNamespace(PROJECT_ID),
        },
        automaton=automaton, state_key="start",
    )
    _updates, snippets, failures = automaton.eval_action_on_exit(automaton.states["start"].actions[0], scope)
    return snippets, failures


def test_a_rendered_attachment_fills_its_expressions_and_leaves_the_rest_of_the_text_as_written(db: Db, tmp_path):
    db.ensure_project(PROJECT_ID)
    db.save_project_files(
        PROJECT_ID, {name: text.encode() for name, text in FILES.items()},
        {"index.yml": "text/yaml", "behaviour/report.md": "text/markdown"},
    )
    db.publish_project(PROJECT_ID)
    revision = db.get_project_revision(PROJECT_ID)
    module_name = module_name_for(PROJECT_ID)
    compile_contents(FILES, module_name, tmp_path, revision).rename(package_dir(tmp_path, module_name, revision))
    compiled = CompiledAutomatonLoader(db, tmp_path).load_at_revision(PROJECT_ID, revision)
    interpreted = AutomatonBuilder().build(FILES)
    interpreted.set_storage_location(revision)

    expected = ('show("# Ana\\n\\nMood: happy \\u2014 100% {x} nombre_usuario")', ())
    assert _shown(db, interpreted) == expected
    assert _shown(db, compiled) == expected
