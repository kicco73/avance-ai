"""Flattening a provider-neutral message content to plain text — pure
text shaping, no LLM behavior, the same kind of zero-dependency utility
token_estimate.py already is. Lives outside both `ai/` and `tracking/`
so a core caller (talker/human_talker.py) can use it without depending
on the ai skill at all."""
from __future__ import annotations

from typing import Any

from system.logging_factory import LoggerFactory

logger = LoggerFactory.get_logger(__name__)


def is_text_fragments(content: Any) -> bool:
    """True for one user message made of several text blocks — the
    fragments of a coalesced turn (see Db.get_turn_history). Tells them
    apart from the other list shape a content can have, the attachment
    blocks content_to_text flattens below."""
    return isinstance(content, list) and bool(content) and all(isinstance(block, str) for block in content)


def content_to_text(content: Any, provider_name: str = "LLM") -> str:
    """Flattens provider-neutral attachment blocks to plain text.
    Binary (base64) attachments are skipped if unsupported. A message of
    several text fragments joins with a newline — for estimating tokens
    only; every provider renders those as real, separate blocks of one
    message (see is_text_fragments's own callers).
    """
    if isinstance(content, str):
        return content
    if is_text_fragments(content):
        return "\n".join(content)
    parts: list[str] = []
    for block in content:
        source = block["source"]
        if source["type"] == "text":
            parts.append(f"[Attachment: {block['filename']}]\n{source['data']}")
        else:
            logger.warning(
                "Skipping unsupported binary attachment '%s' for %s.",
                block["filename"],
                provider_name,
            )
    return "\n\n".join(parts)
