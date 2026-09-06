from __future__ import annotations

from automaton.automaton_builder import AutomatonBuilder
from automaton.automaton_yaml_editor import AutomatonYamlEditor



BASE_YAML = """\
project:
  id: proj
init-action:
  target: a
signals:
  foo:
    ui-label: Foo signal
    definition: foo definition
  bar:
    ui-label: Bar signal
    definition: bar definition
states:
  a:
    ui-label: State A
    contextual-prompt: hi
    actions:
      - name: go-b
        ui-label: Go to B
        target: b
        trigger: signal.foo >= 50
      - name: go-c
        ui-label: Go to C
        target: c
        trigger: signal.foo >= 50 and signal.bar >= 50
  b:
    ui-label: State B
    contextual-prompt: there
  c:
    ui-label: State C
    contextual-prompt: elsewhere
"""


def make_editor(text: str = BASE_YAML) -> AutomatonYamlEditor:
    return AutomatonYamlEditor(text)


def builds(text: str, archives: dict[str, str] | None = None):
    return AutomatonBuilder().build({"index.yml": text, **(archives or {})})


ENV_BASE_YAML = """\
project:
  id: proj
init-action:
  target: a
env:
  visits:
    ui-description: Visit counter
  score: {}
states:
  a:
    ui-label: State A
    contextual-prompt: hi
    actions:
      - name: go-b
        ui-label: Go to B
        target: b
        trigger: env.visits >= 1
      - name: go-c
        ui-label: Go to C
        target: c
        trigger: env.visits >= 1 and env.score >= 50
  b:
    ui-label: State B
    contextual-prompt: there
  c:
    ui-label: State C
    contextual-prompt: elsewhere
"""

SOURCE_BASE_YAML = """\
project:
  id: proj
init-action:
  target: a
sources:
  pino:
    ui-label: Flights
    url: avance:flights.csv
  cities:
    ui-label: Cities
    url: avance:cities.csv
states:
  a:
    ui-label: State A
    contextual-prompt: hi
    actions:
      - name: go-b
        ui-label: Go to B
        target: b
        trigger: source.pino.select('x') != 'nope'
      - name: go-c
        ui-label: Go to C
        target: c
        trigger: source.pino.select('x') != 'nope' and source.cities.select('x') != 'nope'
  b:
    ui-label: State B
    contextual-prompt: there
  c:
    ui-label: State C
    contextual-prompt: elsewhere
"""

SOURCE_ARCHIVES = {"flights.csv": "a,b\n1,2\n", "cities.csv": "city\nParis\n"}
