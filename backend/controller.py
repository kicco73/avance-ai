from __future__ import annotations

import inspect

from fastapi import APIRouter, HTTPException, Request, Response

from automaton.automaton import Automaton
from chat_service import ChatService, ChatServiceError
from model_service import ModelService
from schemas import ActionRequest, AutoTrackingRequest, ChatMessageRequest, TriggersPreviewRequest


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

    @get("/api/signals")
    def get_signals(self):
        """Read-only: never calls the AI. Signals are only (re)computed inside
        the auto-tracking flow (see ChatService._run_auto_tracking); this just
        reports the latest persisted snapshot."""
        return self.chat_service.signals.get_latest_signals()

    @get("/api/state")
    def get_state(self):
        return self.model_service.get_active_state_payload()

    @get("/api/messages")
    async def get_messages(self):
        await self.chat_service.open_if_needed()
        return self.chat_service.get_messages()

    @post("/api/messages")
    async def post_message(self, req: ChatMessageRequest):
        text = req.message.strip()
        if not text:
            raise HTTPException(status_code=400, detail="Message cannot be empty.")
        result = await self.chat_service.process_turn(text)
        return {
            "reply": result.reply,
            "state": result.state,
            "state_changed": result.state_changed,
            "new_state": result.new_state,
            "triggered_action": result.triggered_action,
        }

    @post("/api/action")
    def post_action(self, req: ActionRequest):
        try:
            return self.model_service.apply_manual_action(req.action_name)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @get("/api/autotracking")
    def get_autotracking(self):
        return {"enabled": self.chat_service.auto_tracking_enabled}

    @post("/api/autotracking")
    def post_autotracking(self, req: AutoTrackingRequest):
        self.chat_service.auto_tracking_enabled = req.enabled
        return {"enabled": self.chat_service.auto_tracking_enabled}

    @post("/api/triggers/preview")
    def post_triggers_preview(self, req: TriggersPreviewRequest):
        automaton, state_key = self.model_service.get_active_automaton_and_state()
        return automaton.preview_triggers(state_key, req.signals)

    @post("/api/reset")
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
        commit and swap, restoring `model_name`'s own history if it has one."""
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
