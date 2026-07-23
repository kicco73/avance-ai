"""Pydantic request bodies for the REST endpoints — see controller.py."""
from __future__ import annotations

from pydantic import BaseModel


class ActionRequest(BaseModel):
    action_name: str


class AutoTrackingRequest(BaseModel):
    enabled: bool


class TriggersPreviewRequest(BaseModel):
    signals: dict[str, int | None]


class ChatMessageRequest(BaseModel):
    message: str
