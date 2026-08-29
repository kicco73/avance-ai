from __future__ import annotations

import re
from pathlib import Path

import tinycss2


class CssValidator:
    """index.css-specific validation — syntax errors and url(...) asset
    references — run by ProjectEditor.put_project_file before a save is
    accepted."""

    _URL_PATTERN = re.compile(r"url\(\s*(['\"]?)([^'\")]+)\1\s*\)", re.IGNORECASE)
    _ABSOLUTE_URL_PATTERN = re.compile(r"^(https?:)?//|^data:", re.IGNORECASE)

    # @media/@supports are the only at-rules a chat-widget skin plausibly
    # nests rules inside; @font-face/@page/@import etc. take a declaration
    # list or no block at all, which parse_rule_list would misread as rules.
    _NESTED_RULE_AT_RULES = frozenset({"media", "supports"})

    @classmethod
    def referenced_basenames(cls, css_text: str) -> set[str]:
        names = set()
        for _, target in cls._URL_PATTERN.findall(css_text):
            target = target.strip()
            if not target or cls._ABSOLUTE_URL_PATTERN.match(target):
                continue
            names.add(Path(target).name)
        return names

    @classmethod
    def missing_references(cls, css_text: str, known_archive_names: set[str]) -> list[str]:
        return [name for name in cls.referenced_basenames(css_text) if name not in known_archive_names]

    @classmethod
    def syntax_errors(cls, css_text: str) -> list[str]:
        """Every low-level syntax error tinycss2 finds in `css_text` — an
        unterminated string/block, a malformed selector or at-rule, a
        declaration missing its colon — as "line N: message" strings, empty
        if none. tinycss2 is a syntax-only (CSS Syntax Module) parser, not a
        full CSS engine: it won't flag a nonsense property value like
        `color: bees;`, only genuine malformation."""
        rules = tinycss2.parse_stylesheet(css_text, skip_comments=True, skip_whitespace=True)
        return cls._collect_syntax_errors(rules)

    @classmethod
    def _collect_syntax_errors(cls, nodes: list) -> list[str]:
        """Recurses through a parsed stylesheet/declaration-list/value's
        nodes, collecting every `error` tinycss2 attached anywhere in the
        tree — structural ones (a malformed rule or declaration) sit
        alongside their siblings; tokenization ones (an unterminated
        string/url) sit inside a declaration's own value, which is why this
        walks all the way down rather than stopping at the top level."""
        errors = []
        for node in nodes:
            node_type = getattr(node, "type", None)
            if node_type == "error":
                errors.append(f"line {node.source_line}: {node.message}")
            elif node_type == "qualified-rule" and node.content is not None:
                declarations = tinycss2.parse_declaration_list(node.content, skip_comments=True, skip_whitespace=True)
                errors.extend(cls._collect_syntax_errors(declarations))
            elif node_type == "at-rule" and node.content is not None and node.lower_at_keyword in cls._NESTED_RULE_AT_RULES:
                nested = tinycss2.parse_rule_list(node.content, skip_comments=True, skip_whitespace=True)
                errors.extend(cls._collect_syntax_errors(nested))
            elif node_type == "declaration":
                errors.extend(cls._collect_syntax_errors(node.value))
            elif node_type == "function":
                errors.extend(cls._collect_syntax_errors(node.arguments))
            elif node_type in ("() block", "[] block", "{} block") and node.content is not None:
                errors.extend(cls._collect_syntax_errors(node.content))
        return errors
