from __future__ import annotations

from collections.abc import Mapping

from ruamel.yaml import YAMLError

from automaton import deprecations
from automaton.automaton_yaml_editor import AutomatonYamlEditor
from automaton.builder.project_metadata import load_yaml


class IndexYml:

    def __init__(self, original: str, modernized: str, fixes: tuple[str, ...]) -> None:
        self._original = original
        self._modernized = modernized
        self.fixes = fixes

    @property
    def text(self) -> str:
        return self._original


class ModernizedIndexYml(IndexYml):

    @property
    def text(self) -> str:
        return self._modernized


_OUTCOMES = {False: IndexYml, True: ModernizedIndexYml}

ROUNDS = 10


class IndexYmlModernizer:
    """Rewrites what a build refuses and the format can still account
    for. It runs in rounds until a round finds nothing: one rewrite
    uncovers the next — a script moved off a state onto the actions that
    reach it arrives carrying calls in a namespace that itself has a
    replacement — and detecting everything up front would see only the
    first layer, leaving the rest for a build to refuse. ROUNDS only
    bounds a rewrite that failed to settle; every one of them is
    idempotent, so the loop normally stops one round after the last
    change."""

    def modernize(self, text: str) -> IndexYml:
        modernized, fixes = text, []
        for _ in range(ROUNDS):
            found = deprecations.found_in(self._raw(modernized))
            if not found:
                break
            editor = AutomatonYamlEditor(modernized)
            for deprecation in found:
                deprecation.rewrite(editor)
            modernized = editor.serialize()
            fixes += [deprecation.fix for deprecation in found]
        return _OUTCOMES[modernized != text](text, modernized, tuple(dict.fromkeys(fixes)))

    @staticmethod
    def _raw(text: str) -> Mapping:
        try:
            raw = load_yaml(text)
        except YAMLError:
            return {}
        return raw if isinstance(raw, Mapping) else {}
