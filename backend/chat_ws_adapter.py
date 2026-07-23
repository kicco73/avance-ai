"""Thin /ws/chat adapter over ChatService (see chat_service.py): receives
{message}, calls ChatService.process_turn(), and translates the result
into the existing retrying/done/error frame protocol. No chat-turn domain
logic lives here — only the websocket-specific plumbing around it.
"""
from __future__ import annotations

from fastapi import WebSocket, WebSocketDisconnect

from chat_service import ChatService, ChatServiceError

class ChatWsAdapter(object):
    def __init__(self, chat_service: ChatService) -> None:
        self._chat_service = chat_service

    async def chat_loop(self, websocket: WebSocket) -> None:
        """Accepts the /ws/chat connection and dispatches every non-empty
        frame to ChatService.process_turn(), one at a time (the loop only
        calls receive_json() again once the previous turn is fully done)."""
        await websocket.accept()
        try:
            while True:
                data = await websocket.receive_json()
                text = (data or {}).get("message", "").strip()
                if not text:
                    continue

                async def _push_retrying(attempt: int, max_attempts: int, retry_in: float) -> None:
                    await websocket.send_json({
                        "type": "retrying",
                        "attempt": attempt,
                        "max_attempts": max_attempts,
                        "retry_in": retry_in,
                    })

                try:
                    result = await self._chat_service.process_turn(text, on_retry=_push_retrying)
                except ChatServiceError as exc:
                    await websocket.send_json({
                        "type": "error",
                        "error": {"message": exc.message, "detail": exc.detail},
                    })
                    continue

                await websocket.send_json({
                    "type": "done",
                    "reply": result.reply,
                    "state": result.state,
                    "state_changed": result.state_changed,
                    "new_state": result.new_state,
                    "triggered_action": result.triggered_action,
                })
        except WebSocketDisconnect:
            pass
