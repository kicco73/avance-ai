"""Pydantic request bodies for the REST endpoints — see controller.py."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, field_validator


class LoginRequest(BaseModel):
    provider: str
    credential: str


class AcceptTermsRequest(BaseModel):
    invite_code: str | None = None


class ActuatorsRequest(BaseModel):
    enabled: bool


class AiModelSelectionRequest(BaseModel):
    index: int | None = None


class ExpectedStateRequest(BaseModel):
    expected_state: str | None = None


class ExpectedSignalsRequest(BaseModel):
    expected_values: dict[str, int | float] | None = None


class CommentRequest(BaseModel):
    comment: str | None = None


class SetSessionLabeledRequest(BaseModel):
    labeled: bool


class SetSessionTitleRequest(BaseModel):
    title: str | None = None


class SaveMediaToDriveRequest(BaseModel):
    file_name: str


class SessionImportMessageJson(BaseModel):
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
    origin: str | None = None
    tool_calls: list[dict] | None = None


class SessionImportJsonRequest(BaseModel):
    name: str | None = None
    username: str | None = None
    type: str | None = None
    timestamp: str | None = None
    datetime_end: str | None = None
    start_state: str | None = None
    end_state: str | None = None
    labeled: bool = False
    comment: str | None = None
    closed_at: str | None = None
    close_reason: str | None = None
    messages: list[SessionImportMessageJson] = []


class TruncateSessionRequest(BaseModel):
    timestamp: str


class RateSessionRequest(BaseModel):
    rating: Literal[1, 5]


class SetUserRoleRequest(BaseModel):
    role: Literal["user", "customer", "supervisor", "admin"]


class SetWhatsAppPhoneNumberRequest(BaseModel):
    phone_number: str | None = None
    confirm_merge: bool = False


class SetEnvValueRequest(BaseModel):
    value: str


class SetServiceLevelRequest(BaseModel):
    level: Literal["required", "optional", "disabled"]


class SetProjectFieldRequest(BaseModel):
    value: str | bool | dict[str, str] | list[str]

    @field_validator("value")
    @classmethod
    def _strip_string_value(
        cls, value: str | bool | dict[str, str] | list[str],
    ) -> str | bool | dict[str, str] | list[str]:
        """Trims string values so incidental UI whitespace (e.g. "Action ")
        never creates a duplicate distinct from "Action". A bare boolean
        (history-cutoff/chat) passes through untouched; a mapping (env) or
        a list (ai-may-read-sources/ai-must-read-sources) is stripped
        entry by entry the same way."""
        if isinstance(value, str):
            return value.strip()
        if isinstance(value, dict):
            return {key: entry.strip() for key, entry in value.items()}
        if isinstance(value, list):
            return [entry.strip() for entry in value]
        return value


class RenameProjectFileRequest(BaseModel):
    new_name: str


class AiEditRequest(BaseModel):
    instruction: str


class WebImportRequest(BaseModel):
    query: str


class ReorderActionRequest(BaseModel):
    value: int


class PublishProjectRequest(BaseModel):
    remap_to: str | None = None


class CreateTestRequest(BaseModel):
    session_id: int | None = None
    strategy: str
    username: str | None = None


class StateTestRequest(BaseModel):
    strategy: str


class ReassignSessionsRequest(BaseModel):
    session_ids: list[int]
    username: str
