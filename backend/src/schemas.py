"""Pydantic request bodies for the REST endpoints — see controller.py."""
from __future__ import annotations

from pydantic import BaseModel


class ActionRequest(BaseModel):
    action_name: str
    # The frontend's current session_id (see chat/session_manager.py) —
    # required by convention, but not authoritative: the backend always
    # resolves and enforces the actual writable session server-side.
    session_id: int | None = None


class AutoTrackingRequest(BaseModel):
    enabled: bool


class TriggersPreviewRequest(BaseModel):
    signals: dict[str, int | float | None]


class ChatMessageRequest(BaseModel):
    message: str
    # See ActionRequest.session_id.
    session_id: int | None = None


class AiModelSelectionRequest(BaseModel):
    # None selects auto (the ai-service cascade's own fallback order); an
    # index into GET /api/ai/models' `models` pins generation to that
    # entry directly. See ai/ai_service.py's AiService.select_model.
    index: int | None = None


class ExpectedStateRequest(BaseModel):
    # None clears the annotation — see ChatService.set_message_expected_state.
    expected_state: str | None = None


class ExpectedSignalsRequest(BaseModel):
    # The whole replacement dict — a signal name missing from it is
    # annotation-cleared for that signal alone; None/{} clears every
    # signal's annotation for this message. See
    # ChatService.set_message_expected_signals.
    expected_values: dict[str, int | float] | None = None
