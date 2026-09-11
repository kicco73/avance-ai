"""The inbound half of Meta's wire: what it POSTs to us, and whether to
believe it.

cloud_api_client.py is the other half — what we POST to Meta. Both are
the Cloud API and neither is a conversation: parsing an envelope,
checking an HMAC and recognising a redelivery are things that would read
the same if the thing on the other end of them were not a chat at all.

Kept out of whatsapp_service.py because that is meant to be a router —
it posts what a person said and delivers what comes back — and none of
this is either.
"""
from __future__ import annotations

import hashlib
import hmac
import re
import time
from dataclasses import dataclass


@dataclass(frozen=True)
class IncomingMessage:
    id: str
    sender: str  # E.164 digits, no '+', as Meta sends it
    type: str
    text: str | None
    action_id: str | None = None
    # Meta's media id for an `audio` message (a voice note or an audio
    # file — both arrive as type "audio"); downloaded on demand.
    audio_id: str | None = None


def is_valid_verify_token(token: str, expected: str) -> bool:
    return hmac.compare_digest(token, expected)


def is_valid_signature(raw_body: bytes, header: str | None, app_secret: str) -> bool:
    """X-Hub-Signature-256: 'sha256=' + HMAC-SHA256(app secret, raw body)."""
    if not header or not header.startswith("sha256="):
        return False
    expected = hmac.new(app_secret.encode(), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, header[len("sha256="):])


def extract_incoming(payload: dict) -> list[IncomingMessage]:
    """Every inbound message in a webhook payload. Status updates
    (delivered/read receipts) share the same envelope but live under
    `statuses`, not `messages`, so they simply never show up here."""
    out: list[IncomingMessage] = []
    for entry in payload.get("entry", []) or []:
        for change in entry.get("changes", []) or []:
            value = change.get("value") or {}
            if value.get("messaging_product") != "whatsapp":
                continue
            for msg in value.get("messages", []) or []:
                message_id, sender = msg.get("id"), msg.get("from")
                if not message_id or not sender:
                    continue
                msg_type = msg.get("type") or ""
                text = (msg.get("text") or {}).get("body") if msg_type == "text" else None
                audio_id = (msg.get("audio") or {}).get("id") if msg_type == "audio" else None
                action_id = None
                if msg_type == "interactive":
                    interactive = msg.get("interactive") or {}
                    reply = interactive.get("button_reply") or interactive.get("list_reply")
                    action_id = reply.get("id") if reply else None
                out.append(IncomingMessage(
                    id=message_id, sender=sender, type=msg_type, text=text, action_id=action_id,
                    audio_id=audio_id,
                ))
    return out


class SeenMessages(object):
    """Meta retries whenever the webhook did not answer 200 fast enough,
    so the same message arrives more than once. Ids only, with a ttl:
    there is no acknowledgement to wait for and nothing to persist — a
    restart losing this means at worst one duplicate reply."""

    def __init__(self, ttl_seconds: int = 3600) -> None:
        self._ttl = ttl_seconds
        self._seen: dict[str, float] = {}

    def check_and_add(self, message_id: str) -> bool:
        now = time.monotonic()
        if len(self._seen) > 5000:
            self._seen = {k: t for k, t in self._seen.items() if now - t < self._ttl}
        if message_id in self._seen and now - self._seen[message_id] < self._ttl:
            return False
        self._seen[message_id] = now
        return True


_HEADING = re.compile(r"^[ \t]{0,3}#{1,6}[ \t]+(.+?)[ \t]*#*[ \t]*$", re.MULTILINE)
_BOLD = re.compile(r"(\*\*|__)(.+?)\1", re.DOTALL)
_LINK = re.compile(r"\[([^\]]+)\]\((\S+?)\)")
_BULLET = re.compile(r"^(\s*)[*+]\s+", re.MULTILINE)


def to_whatsapp_markdown(text: str) -> str:
    """The model writes CommonMark (see docs/MARKDOWN_GUIDE.md); WhatsApp
    only renders *bold*, _italic_, ~strike~, ```mono``` and '- ' lists.
    Headings become bold lines, links are spelled out, '*' bullets become
    '-' so they aren't mistaken for bold markers."""
    text = _LINK.sub(r"\1 (\2)", text)
    text = _BOLD.sub(r"*\2*", text)
    text = _HEADING.sub(lambda m: f"*{m.group(1).strip('*_ ')}*", text)
    text = _BULLET.sub(r"\1- ", text)
    return text.strip()
