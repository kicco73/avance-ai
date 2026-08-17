from __future__ import annotations


class TagPromptBuilder:
    """Resolves a list of (tag, template_key) pairs into {tag: template}
    by looking each template_key up in a caller-supplied templates dict.
    A prerequisite for benchmark replay strategies (not introduced here)
    — no feature/behavior change on its own."""

    def build(self, tag_specs: list[tuple[str, str]], templates: dict[str, str]) -> dict[str, str]:
        result: dict[str, str] = {}
        for tag, template_key in tag_specs:
            result[tag] = templates[template_key]
        return result
