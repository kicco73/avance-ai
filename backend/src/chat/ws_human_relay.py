"""WsHumanRelay — the HumanRelay (see talker.human_talker) that reaches
a person through their own WsNotifications connections: the same
websocket their browser already has open for chat, reused for
human_prompt/human_reply frames instead of a dedicated channel.

This is deliberately the simplest possible relay, built for testing
HumanTalker by hand rather than for the eventual real feature (a
distinct operator, WhatsApp as an alternative channel, a switch that
survives a restart): it broadcasts to *every* connection the target
identity has open, so the two-tabs-same-account setup WsNotifications'
own per-role connection cap makes room for (see MAX_CONNECTIONS_PER_
ADMIN) is enough to answer your own session from a second tab.
"""
from __future__ import annotations

from typing import AsyncIterator

from session import Session

from .ws_notifications import WsNotifications


class WsHumanRelay:
    def __init__(
        self,
        ws_notifications: WsNotifications,
        username: str,
        session_id: int,
        session_type: str | None = None,
        project_id: int | None = None,
    ) -> None:
        self._ws_notifications = ws_notifications
        self._username = username
        self._session_id = session_id
        self._session_type = session_type
        self._project_id = project_id

    async def notify(self, prompt_text: str) -> None:
        # Best-effort: exclude the connection running this turn (the tab
        # that just sent the message) from also seeing its own prompt.
        # Session().connection_id is None for anything that didn't come
        # in over a websocket (e.g. WhatsApp) — nothing to exclude then.
        self._prompt_id = await self._ws_notifications.send_human_prompt(
            self._username,
            self._session_id,
            prompt_text,
            session_type=self._session_type,
            project_id=self._project_id,
            exclude_connection_id=Session().connection_id,
        )

    async def receive(self) -> str:
        return await self._ws_notifications.await_human_reply(self._prompt_id)

    async def recorded_audio(self, text: str) -> AsyncIterator[bytes] | None:
        # No original-recording store for this relay yet — a spoken human
        # reply always falls back to whatever the session's own TalkService
        # would say (see AiTalker.talk(), used as a fallback where this
        # relay is wired in), never silently to text.
        return None
