from __future__ import annotations

import inspect

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import StreamingResponse

from automaton.automaton import Automaton
from chat_service import ChatService, ChatServiceError
from model_service import ModelService
from schemas import (
    ActionRequest,
    AudioEnabledRequest,
    AutoTrackingRequest,
    ChatMessageRequest,
    TriggersPreviewRequest,
)


def route(method: str, path: str, **kwargs):
    def decorator(func):
        func.__route_info__ = (method, path, kwargs)
        return func
    return decorator


def get(path: str, **kwargs):
    return route("GET", path, **kwargs)


def post(path: str, **kwargs):
    return route("POST", path, **kwargs)


def put(path: str, **kwargs):
    return route("PUT", path, **kwargs)


def delete(path: str, **kwargs):
    return route("DELETE", path, **kwargs)


class AvanceController(object):
    def __init__(
        self,
        chat_service: ChatService,
        model_service: ModelService,
    ) -> None:
        self.chat_service = chat_service
        self.model_service = model_service

        self.router = APIRouter()
        for _, member in inspect.getmembers(self, predicate=inspect.ismethod):
            info = getattr(member, "__route_info__", None)
            if info is not None:
                method, path, kwargs = info
                self.router.add_api_route(path, member, methods=[method], **kwargs)

    @get("/api/chat/signals")
    def get_signals(self):
        """Read-only: never calls the AI. Signals are only (re)computed inside
        the auto-tracking flow (see ChatService._run_auto_tracking); this just
        reports the latest persisted snapshot."""
        return self.chat_service.signals.get_latest_signals()

    @get("/api/state")
    def get_state(self):
        return self.model_service.get_active_state_payload()

    @get("/api/chat/messages")
    async def get_messages(self):
        return await self.chat_service.get_messages()

    @post("/api/chat/messages")
    async def post_message(self, req: ChatMessageRequest):
        text = req.message.strip()
        if not text:
            raise HTTPException(status_code=400, detail="Message cannot be empty.")
        return await self.chat_service.process_turn(text)

    @post("/api/action")
    async def post_action(self, req: ActionRequest):
        try:
            return await self.chat_service.apply_manual_action(req.action_name)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @get("/api/chat/autotracking")
    def get_autotracking(self):
        return {"enabled": self.chat_service.auto_tracking_enabled}

    @post("/api/chat/autotracking")
    def post_autotracking(self, req: AutoTrackingRequest):
        self.chat_service.auto_tracking_enabled = req.enabled
        return {"enabled": self.chat_service.auto_tracking_enabled}

    @get("/api/chat/audio")
    def get_audio_enabled(self):
        return {"enabled": self.chat_service.audio_enabled}

    @post("/api/chat/audio")
    def post_audio_enabled(self, req: AudioEnabledRequest):
        self.chat_service.audio_enabled = req.enabled
        return {"enabled": self.chat_service.audio_enabled}

    @get("/api/chat/messages/{message_id}/audio")
    def get_message_audio(self, message_id: int):
        """If generation for message_id is still in flight right now,
        streams it chunk by chunk as ChatService's background task
        produces them (see AudioStore.LiveAudioGeneration) instead of
        waiting for the complete file — lower latency for a client that
        happens to arrive while it's still running. Otherwise, exactly as
        before: the completed file from disk, or 404 if there isn't one
        (never generated, wrong provider/toggle at the time, or already
        purged). The frontend treats a 404 here as "no audio available",
        not a failure to surface (see api.js's messageAudioUrl)."""
        live = self.chat_service.get_live_audio_generation(message_id)
        if live is not None:
            return StreamingResponse(live.stream_from(0), media_type="audio/wav")

        audio = self.chat_service.get_message_audio(message_id)
        if audio is None:
            raise HTTPException(status_code=404, detail="No audio available for this message.")
        return Response(content=audio, media_type="audio/wav")

    @post("/api/triggers/preview")
    def post_triggers_preview(self, req: TriggersPreviewRequest):
        automaton, state = self.model_service.get_active_automaton_and_state()
        return automaton.preview_triggers(state.key, req.signals)

    @post("/api/chat/reset")
    async def post_reset(self):
        async with self.chat_service.lock:
            self.model_service.reset_active_model()
            self.chat_service.auto_tracking_enabled = True
        return self.model_service.get_active_state_payload()

    @get("/api/models")
    def get_models(self):
        return self.model_service.list_models()

    @put("/api/models/{model_name}/activate")
    async def activate_model(self, model_name: str):
        try:
            await self.model_service.activate_model_idempotent(model_name, self._activate_model)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {
            "success": True,
            "model_name": model_name,
        }

    @get("/api/models/{model_name}")
    def get_model(self, model_name: str):
        """Downloads `model_name` as a zip — the read side of PUT
        /api/models/{model_name}, built so it round-trips back through PUT with
        no transformation. Not restricted to the active model."""
        try:
            content = self.model_service.export_model_zip(model_name)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return Response(
            content=content,
            media_type="application/zip",
            headers={"Content-Disposition": f'attachment; filename="{model_name}.zip"'},
        )

    @put("/api/models/{model_name}")
    async def put_model(self, model_name: str, request: Request):
        """Creates or replaces `model_name` from a raw body (YAML or zip, see
        models_manager._looks_like_zip). Stage -> validate -> only on success
        commit, swap, and wipe `model_name`'s prior conversation data."""
        content = await request.body()
        content_type = request.headers.get("content-type")

        try:
            result = await self.model_service.put_model(model_name, content, content_type, self._activate_model)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @delete("/api/models/{model_name}")
    async def delete_model(self, model_name: str):

        try:
            await self.model_service.delete_model(model_name, self._activate_model)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        except OSError as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc
        return {"success": True}

    async def _activate_model(self, new_automaton: Automaton) -> None:
        # Unused: kept only to match ModelsManager's CommitCallback shape.
        async with self.chat_service.lock:
            self.chat_service.auto_tracking_enabled = True
