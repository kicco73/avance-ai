from __future__ import annotations

from logging_factory import LoggerFactory
from service_error import ServiceError
from .chat_service import ChatService
from .tool_status_text import tool_status_text

logger = LoggerFactory.get_logger(__name__)


class WsChatTurn(object):
    """One `turn` frame's whole life: the user message persisted the
    moment the frame is read (accept), then the turn run as its own task
    (run), every frame it produces sent on the connection with this
    turn's own turn_id — the only correlation there is."""

    def __init__(self, chat_service: ChatService, connection, turn_id: str, session_id, text: str) -> None:
        self._chat_service = chat_service
        self._connection = connection
        self._turn_id = turn_id
        self._session_id = session_id
        self._text = text
        self._user_message_id: int | None = None

    def _send(self, frame_type: str, payload: dict) -> None:
        self._connection.send({"type": frame_type, "turn_id": self._turn_id, **payload})

    def _send_error(self, exc: ServiceError) -> None:
        data = {"message": exc.message, "detail": getattr(exc, "detail", str(exc))}
        if exc.code is not None:
            data["code"] = exc.code
        self._send("error", data)

    def on_metadata(self, key: str, value) -> None:
        if key == "audio":
            self._send("audio_text", {"content": value})
        elif key == "chunk":
            self._send("chunk", {"content": value})
        elif key == "typing":
            # A live "someone is composing a reply" signal — sent once,
            # right before real generation starts for the model (see
            # TrackingProcessor.process), or only once an operator's own
            # human_typing frame arrives for a human-answered turn (see
            # ChatService._process_human_turn, talker.human_talker.
            # HumanTalker.chat). Never inferred client-side from an empty
            # message any more (see MessageBubble.vue's own awaitingReply).
            self._send("typing", {"session_id": self._session_id})
        elif key == "text":
            self._send("text", {"content": value})
        elif key == "tool":
            # One frame type, "tool", for both phases — the frontend's own
            # reader (chatClient.js) tells them apart by data.phase.
            # status_text is only ever meaningful on "start" (see
            # tool_status_text's own docstring); "result" carries the
            # payload verbatim.
            payload = {**value, "status_text": tool_status_text(value)} if value["phase"] == "start" else value
            self._send("tool", payload)

    def accept(self) -> bool:
        text = self._text.strip()
        try:
            if not isinstance(self._session_id, int):
                raise ServiceError("Session not found.", status_code=404, code="session_not_found")
            if not text:
                raise ServiceError("Message cannot be empty.", status_code=400, code="empty_message")
            self._user_message_id = self._chat_service.accept_user_message(self._session_id, text)
        except ServiceError as exc:
            self._send_error(exc)
            return False
        self._text = text
        return True

    async def run(self) -> None:
        try:
            result = await self._chat_service.process_turn(
                self._session_id, self._text, on_metadata=self.on_metadata, user_message_id=self._user_message_id,
            )
            self._send("done", result)
        except ServiceError as exc:
            self._send_error(exc)
        except Exception as exc:
            logger.exception(f"Unexpected error while processing a chat turn: {str(exc)}")
            self._send("error", {"message": "Unexpected server error.", "detail": str(exc)})
