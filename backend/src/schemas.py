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
    session_id: int


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


class TruncateSessionRequest(BaseModel):
    # ISO 8601, expected to be one of the UTC-explicit strings the
    # backend itself already handed back (see db._utc_iso) — every
    # Message/Tracking row at or after this instant is deleted. See
    # ChatService.truncate_session.
    timestamp: str


class SetEnvValueRequest(BaseModel):
    # See ChatService.set_env_value/chat.env.Env.set_value — the
    # Inspector Env tab's own "click a value to edit it".
    value: str


class SetProjectFieldRequest(BaseModel):
    # See ProjectService.set_state_field/set_action_field/
    # set_signal_field — a state/action/signal's own editable fields are
    # either free text (ui-label, contextual-prompt, action-prompt,
    # definition, target) or, for a state's own history-cutoff, a plain
    # boolean.
    value: str | bool


class ReorderActionRequest(BaseModel):
    # 0-based index the action should end up at, in its own state's
    # actions list — see ProjectService.reorder_actions/
    # AutomatonYamlEditor.reorder_actions.
    value: int
