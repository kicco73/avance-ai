"""Pydantic request bodies for the REST endpoints — see controller.py."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, field_validator


class LoginRequest(BaseModel):
    # 'google' for now — see auth/auth_service.py's own provider registry.
    provider: str
    # The provider's own opaque credential (Google: the Identity Services
    # ID token) — verified by AuthProvider.verify(), never inspected here.
    credential: str


class AcceptTermsRequest(BaseModel):
    # The invite code a "share project" link carries (see
    # frontend/src/shareLink.js) — self-registration is only allowed
    # when this clears AuthService.complete_registration's own
    # ProjectService.validate_invite_for_registration check (exists, not
    # expired, under its max-shares budget). None for a plain sign-in
    # with no invite context, which registration now refuses.
    invite_code: str | None = None


class ActionRequest(BaseModel):
    action_name: str


class AutoTrackingRequest(BaseModel):
    enabled: bool


class ChatMessageRequest(BaseModel):
    message: str


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


class CommentRequest(BaseModel):
    # None (or empty/whitespace-only) clears the comment — see
    # ChatService.set_message_comment.
    comment: str | None = None


class ReactionRequest(BaseModel):
    # None clears the reaction — see ChatService.set_message_reaction.
    reaction: str | None = None


class SetSessionLabeledRequest(BaseModel):
    # See ChatService.mark_session_labeled — the "Label sessions" view's
    # own "Mark done" button, a domain expert's explicit, toggleable
    # verdict on whether a session's been reviewed.
    labeled: bool


class SetSessionTitleRequest(BaseModel):
    # None (or empty/whitespace-only) clears it back to unset — see
    # ChatService.set_session_title.
    title: str | None = None


class SessionImportMessageJson(BaseModel):
    # See tracking.session_export.SessionExportManager._export_message —
    # the exact shape "Download all" produces and TrackingService.
    # import_session_json restores. Fields past role/text are optional.
    role: str
    text: str
    timestamp: str | None = None
    audio_text: str | None = None
    tokens: int | None = None
    old_state: str | None = None
    action: str | None = None
    new_state: str | None = None
    values: dict[str, int | float | None] | None = None
    expected_state: str | None = None
    expected_values: dict[str, int | float | None] | None = None
    comment: str | None = None


class SessionImportJsonRequest(BaseModel):
    # See tracking.session_export.SessionExportManager's own
    # _export_session — one entry of the array "Download all" produces.
    name: str | None = None
    username: str | None = None
    type: str | None = None
    timestamp: str | None = None
    datetime_end: str | None = None
    start_state: str | None = None
    end_state: str | None = None
    labeled: bool = False
    comment: str | None = None
    messages: list[SessionImportMessageJson] = []


class TruncateSessionRequest(BaseModel):
    # ISO 8601, expected to be one of the UTC-explicit strings the
    # backend already handed back (see db._utc_iso). Every Message/
    # Tracking row at or after this instant is deleted.
    timestamp: str


class SetUserRoleRequest(BaseModel):
    # See AuthService.set_user_role — UserController's own admin-only
    # role-change endpoint (Manage Users' role badge).
    role: Literal["user", "supervisor", "admin"]


class SetEnvValueRequest(BaseModel):
    # See ChatService.set_env_value/chat.env.Env.set_value — the
    # Inspector Env tab's own "click a value to edit it".
    value: str


class SetProjectFieldRequest(BaseModel):
    # See ProjectService.set_state_field/set_action_field/set_signal_field.
    # Editable fields are free text (ui-label, contextual-prompt, etc.) or,
    # for a state's history-cutoff/chat, a plain boolean.
    value: str | bool

    @field_validator("value")
    @classmethod
    def _strip_string_value(cls, value: str | bool) -> str | bool:
        """Trims string values so incidental UI whitespace (e.g. "Action ")
        never creates a duplicate distinct from "Action". A bare boolean
        (history-cutoff/chat) passes through untouched."""
        return value.strip() if isinstance(value, str) else value


class ReorderActionRequest(BaseModel):
    # 0-based index the action should end up at, in its own state's
    # actions list — see ProjectService.reorder_actions/
    # AutomatonYamlEditor.reorder_actions.
    value: int


class PublishProjectRequest(BaseModel):
    # Required only when ProjectService.preview_publish reports
    # needs_remap — the replacement state a human picked for one that's
    # gone missing from the revision being published. None otherwise.
    remap_to: str | None = None


class CreateTestRequest(BaseModel):
    # None = every labeled session of the project, replayed as one run
    # (same session_id=None|int dual as BenchmarkCalculator). See
    # TestService.create_run.
    session_id: int | None = None
    # 'batch_lite', 'batch', or 'turn_by_turn' — see test_service.py's own
    # VALID_STRATEGIES.
    strategy: str
    # None = the requesting user (Session().user), the default. Set to
    # scope the run to a different user's sessions instead.
    username: str | None = None


class StateTestRequest(BaseModel):
    # See TestService.start_job — same VALID_STRATEGIES as
    # CreateTestRequest.strategy.
    strategy: str


class ReassignSessionsRequest(BaseModel):
    session_ids: list[int]
    username: str
