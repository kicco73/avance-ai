"""Pydantic request bodies for the REST endpoints — see controller.py."""
from __future__ import annotations

from pydantic import BaseModel, field_validator


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


class SetSessionLabeledRequest(BaseModel):
    # See ChatService.mark_session_labeled — the "Label sessions" view's
    # own "Mark done" button, a domain expert's explicit, toggleable
    # verdict on whether a session's been reviewed.
    labeled: bool


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
    # definition, target, trigger) or, for a state's own history-cutoff/
    # chat, a plain boolean.
    value: str | bool

    @field_validator("value")
    @classmethod
    def _strip_string_value(cls, value: str | bool) -> str | bool:
        """Every put_*_field endpoint (state/action/signal/init-action)
        shares this one request body — trimming a string value here,
        the single point they all pass through, means incidental
        leading/trailing whitespace from a UI text field can never make
        "Action " and "Action" register as two distinct ui-labels (or
        otherwise-identical values), regardless of which specific field
        it came in on. A bare boolean (history-cutoff/chat) passes
        through untouched."""
        return value.strip() if isinstance(value, str) else value


class ReorderActionRequest(BaseModel):
    # 0-based index the action should end up at, in its own state's
    # actions list — see ProjectService.reorder_actions/
    # AutomatonYamlEditor.reorder_actions.
    value: int


class PublishProjectRequest(BaseModel):
    # Required only when ProjectService.preview_publish reports
    # needs_remap — the state a human picked for the currently persisted
    # state that's gone missing from the revision about to be published.
    # None (the default, and the only valid value when no remap is
    # needed) means "no remap decision to make".
    remap_to: str | None = None


class CreateBenchmarkRunRequest(BaseModel):
    # None = every labeled session of the project, replayed as one run
    # (same session_id=None|int dual as BenchmarkCalculator). See
    # BenchmarkRunService.create_run.
    session_id: int | None = None
    # 'turn_by_turn' or 'batch' — see benchmark_run_service.py's own
    # VALID_STRATEGIES.
    strategy: str


class StateTestRequest(BaseModel):
    # See BenchmarkRunService.start_job — same VALID_STRATEGIES as
    # CreateBenchmarkRunRequest.strategy.
    strategy: str
