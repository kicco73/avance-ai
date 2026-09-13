from __future__ import annotations

import re

_HEADING = re.compile(r"^[ \t]{0,3}#{1,6}[ \t]+(.+?)[ \t]*#*[ \t]*$", re.MULTILINE)
_BOLD = re.compile(r"(\*\*|__)(.+?)\1", re.DOTALL)
_LINK = re.compile(r"\[([^\]]+)\]\((\S+?)\)")
_BULLET = re.compile(r"^(\s*)[*+]\s+", re.MULTILINE)


def to_whatsapp_markdown(text: str) -> str:
    text = _LINK.sub(r"\1 (\2)", text)
    text = _BOLD.sub(r"*\2*", text)
    text = _HEADING.sub(lambda m: f"*{m.group(1).strip('*_ ')}*", text)
    text = _BULLET.sub(r"\1- ", text)
    return text.strip()
