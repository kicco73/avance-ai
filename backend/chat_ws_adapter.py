"""Thin /ws/chat adapter over ChatService (see chat_service.py): receives
{message}, calls ChatService.process_turn(), and translates the result
into the existing retrying/done/error frame protocol. No chat-turn domain
logic lives here — only the websocket-specific plumbing around it.
"""
from __future__ import annotations

import logging

from fastapi import WebSocket, WebSocketDisconnect

from ai.llm_provider import LLMProviderError
from chat_service import ChatService, ChatServiceError

logger = logging.getLogger(__name__)

class ChatWsAdapter(object):
    def __init__(self, chat_service: ChatService) -> None:
        self._chat_service = chat_service
        # Single-user prototype: at most one connection matters, so a lone
        # reference is enough for push_model_updated() to reach it — see
        # model_watcher.py, the only caller of that method.
        self._active_socket: WebSocket | None = None

    async def chat_loop(self, websocket: WebSocket) -> None:
        """Accepts the /ws/chat connection and dispatches every non-empty
        frame to ChatService.process_turn(), one at a time (the loop only
        calls receive_json() again once the previous turn is fully done)."""
        await websocket.accept()
        self._active_socket = websocket
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
                except (ChatServiceError, LLMProviderError) as exc:
                    # ChatServiceError never reaches FastAPI's global exception
                    # handlers here (those only apply to HTTP requests, not
                    # websocket scope), so LLMProviderError needs the same
                    # explicit translation into an 'error' frame.
                    await websocket.send_json({
                        "type": "error",
                        "error": {"message": exc.message, "detail": exc.detail},
                    })
                    continue
                except Exception as exc:
                    # Anything else unforeseen: without this, the exception
                    # propagates past this inner try, past the outer one
                    # (which only catches WebSocketDisconnect), and kills the
                    # loop — the socket dies and every future message on it
                    # would fail the same way until the client reconnects.
                    logger.exception(f"Unexpected error while processing a chat turn: {str(exc)}")
                    await websocket.send_json({
                        "type": "error",
                        "error": {"message": "Unexpected server error.", "detail": str(exc)},
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
        finally:
            if self._active_socket is websocket:
                self._active_socket = None

    async def push_model_updated(self, model_name: str) -> None:
        """Unsolicited push for a reset the file watcher caused, not a
        request this connection made (see model_watcher.py) — only sent
        when `model_name` was the active model and a client is currently
        connected. No-op otherwise; there's no queueing/retry for a
        client that isn't there right now."""
        websocket = self._active_socket
        if websocket is None:
            return
        try:
            await websocket.send_json({"type": "model_updated", "model_name": model_name})
        except Exception:
            logger.warning("Failed to push model_updated for '%s' to the connected client.", model_name)
