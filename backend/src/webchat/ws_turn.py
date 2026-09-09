from __future__ import annotations

from system.bus import OUTPUT_SPEECH, OUTPUT_TEXT, TURN_ENDED, TURN_FAILED, TURN_STARTED, TURN_TOOL
from system.logging_factory import LoggerFactory
from system.service_error import ServiceError
from turn.turn_service import TurnService
from turn.tool_status_text import tool_status_text

logger = LoggerFactory.get_logger(__name__)


class WsChatTurn(object):
    """One inbound `input.text` frame's whole life: the user message
    persisted the moment the frame is read (accept), then the turn run as
    its own task (run), every frame it produces sent on the connection
    with this turn's own stream_id — the only correlation there is."""

    def __init__(self, turn_service: TurnService, send, turn_id: str, session_id, text: str) -> None:
        self._turn_service = turn_service
        self._send_frame = send
        self._turn_id = turn_id
        self._session_id = session_id
        self._text = text
        self._user_message_id: int | None = None

    def _send(self, frame_type: str, payload: dict) -> None:
        # The wire carries the Bus's own type names and field names —
        # nothing is translated on the way out, so a listener and a
        # browser read the same message (see bus.py).
        self._send_frame({"type": frame_type, "stream_id": self._turn_id, **payload})

    def _send_error(self, exc: ServiceError) -> None:
        data = {"message": exc.message, "detail": getattr(exc, "detail", str(exc))}
        if exc.code is not None:
            data["code"] = exc.code
        self._send(TURN_FAILED, data)

    def on_metadata(self, key: str, value) -> None:
        if key == "audio":
            self._send(OUTPUT_SPEECH, {"body": value})
        elif key == "chunk":
            self._send(OUTPUT_TEXT, {"body": value})
        elif key == "typing":
            # A live "someone is composing a reply" signal — sent once,
            # right before real generation starts for the model (see
            # TrackingProcessor.process), or only once an operator's own
            # human_typing frame arrives for a human-answered turn (see
            # TurnService._process_human_turn, talker.human_talker.
            # HumanTalker.chat). Never inferred client-side from an empty
            # message any more (see MessageBubble.vue's own awaitingReply).
            self._send(TURN_STARTED, {"session_id": self._session_id})
        elif key == "tool":
            # One frame type, "tool", for both phases — the frontend's own
            # reader (chatClient.js) tells them apart by data.phase.
            # status_text is only ever meaningful on "start" (see
            # tool_status_text's own docstring); "result" carries the
            # payload verbatim.
            payload = {**value, "status_text": tool_status_text(value)} if value["phase"] == "start" else value
            self._send(TURN_TOOL, payload)

    def accept(self) -> bool:
        text = self._text.strip()
        try:
            if not isinstance(self._session_id, int):
                raise ServiceError("Session not found.", status_code=404, code="session_not_found")
            if not text:
                raise ServiceError("Message cannot be empty.", status_code=400, code="empty_message")
            self._user_message_id = self._turn_service.accept_user_message(self._session_id, text)
        except ServiceError as exc:
            self._send_error(exc)
            return False
        self._text = text
        return True

    async def run(self) -> None:
        try:
            result = await self._turn_service.process_turn(
                self._session_id, self._text, on_metadata=self.on_metadata, user_message_id=self._user_message_id,
            )
            self._send(TURN_ENDED, result)
        except ServiceError as exc:
            self._send_error(exc)
        except Exception as exc:
            logger.exception(f"Unexpected error while processing a chat turn: {str(exc)}")
            self._send(TURN_FAILED, {"message": "Unexpected server error.", "detail": str(exc)})
