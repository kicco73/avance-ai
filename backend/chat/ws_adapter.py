"""Thin /ws/chat adapter over ChatService (see chat_service.py): receives
{message}, calls ChatService.process_turn(), and translates the result
into the existing retrying/done/error frame protocol. No chat-turn domain
logic lives here — only the websocket-specific plumbing around it.
"""
from __future__ import annotations

import logging

from fastapi import WebSocket, WebSocketDisconnect

from ai.llm_provider import AIServiceError
from chat.chat_service import ChatService, ChatServiceError

logger = logging.getLogger(__name__)

class WsAdapter(object):
    def __init__(self, chat_service: ChatService) -> None:
        self._chat_service = chat_service
        # Single-user prototype: at most one connection matters.
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
                except (ChatServiceError, AIServiceError) as exc:
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

                await websocket.send_json({"type": "done", **result})
        except WebSocketDisconnect:
            pass
        finally:
            if self._active_socket is websocket:
                self._active_socket = None
