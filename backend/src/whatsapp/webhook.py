from __future__ import annotations

import hashlib
import hmac
import time
from dataclasses import dataclass

from whatsapp import notices


@dataclass(frozen=True)
class IncomingMessage(object):
    id: str
    sender: str
    type: str
    text: str | None = None
    action_id: str | None = None
    audio_id: str | None = None

    def invite_code(self) -> str | None:
        return None

    async def routed(self, conversation) -> None:
        await conversation.notify(notices.UNSUPPORTED)


@dataclass(frozen=True)
class TextMessage(IncomingMessage):

    def invite_code(self) -> str | None:
        for token in (self.text or "").strip().split()[-1:]:
            return token
        return None

    async def routed(self, conversation) -> None:
        await conversation.said((self.text or "").strip())


@dataclass(frozen=True)
class ButtonPress(IncomingMessage):

    async def routed(self, conversation) -> None:
        await conversation.tapped(str(self.action_id))


@dataclass(frozen=True)
class VoiceNote(IncomingMessage):

    async def routed(self, conversation) -> None:
        await conversation.spoke(self)


def is_valid_verify_token(token: str, expected: str) -> bool:
    return hmac.compare_digest(token, expected)


def is_valid_signature(raw_body: bytes, header: str | None, app_secret: str) -> bool:
    if not header or not header.startswith("sha256="):
        return False
    expected = hmac.new(app_secret.encode(), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, header[len("sha256="):])


def extract_incoming(payload: dict) -> list[IncomingMessage]:
    out: list[IncomingMessage] = []
    for entry in payload.get("entry", []) or []:
        for change in entry.get("changes", []) or []:
            value = change.get("value") or {}
            if value.get("messaging_product") != "whatsapp":
                continue
            for msg in value.get("messages", []) or []:
                message = _message_of(msg)
                if message is not None:
                    out.append(message)
    return out


def _message_of(msg: dict) -> IncomingMessage | None:
    message_id, sender = msg.get("id"), msg.get("from")
    if not message_id or not sender:
        return None
    msg_type = msg.get("type") or ""
    text = (msg.get("text") or {}).get("body") if msg_type == "text" else None
    audio_id = (msg.get("audio") or {}).get("id") if msg_type == "audio" else None
    action_id = _action_id(msg) if msg_type == "interactive" else None
    return _KINDS[_kind_of(msg_type, text, audio_id, action_id)](
        id=message_id, sender=sender, type=msg_type, text=text, action_id=action_id, audio_id=audio_id,
    )


def _kind_of(msg_type: str, text: str | None, audio_id: str | None, action_id: str | None) -> str:
    carried = {"text": (text or "").strip(), "audio": audio_id, "interactive": action_id}
    return {True: msg_type, False: ""}[bool(carried.get(msg_type))]


def _action_id(msg: dict) -> str | None:
    interactive = msg.get("interactive") or {}
    reply = interactive.get("button_reply") or interactive.get("list_reply")
    return reply.get("id") if reply else None


_KINDS = {
    "text": TextMessage,
    "audio": VoiceNote,
    "interactive": ButtonPress,
    "": IncomingMessage,
}


class SeenMessages(object):

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
