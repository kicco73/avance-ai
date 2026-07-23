"""Every REST/websocket endpoint, as methods on one AvanceController
instance built once in main.py. @get/@post/@put/@delete only tag a method
with its route info at class-definition time; __init__ reads those tags
off the bound methods and registers them on self.router."""
from __future__ import annotations

import inspect

from fastapi import APIRouter, HTTPException, Request, Response

from automaton.automaton import Automaton
from chat_service import ChatService, ChatServiceError
from models_manager import ModelsManager
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
        models_manager: ModelsManager,
        db,
    ) -> None:
        self.chat_service = chat_service
        self.models_manager = models_manager
        self.db = db

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
        return self._state_payload()

    @get("/api/messages")
    def get_messages(self):
        """Persisted conversation history, for the frontend to redisplay after a
        reload/backend restart — including the opening message, regardless of
        which chat transport (if any) is available for turns."""
        return self.chat_service.get_messages()

    @post("/api/messages")
    async def post_message(self, req: ChatMessageRequest):
        """Synchronous REST alternative to /ws/chat, always mounted regardless
        of CHAT_TRANSPORT — the frontend's always-available fallback once it's
        determined the websocket isn't. No retrying-progress notifications
        (see ChatService.process_turn's on_retry): retries still happen
        server-side, just silently from this transport's point of view."""
        text = req.message.strip()
        if not text:
            raise HTTPException(status_code=400, detail="Message cannot be empty.")
        try:
            result = await self.chat_service.process_turn(text)
        except ChatServiceError as exc:
            raise HTTPException(
                status_code=exc.status_code, detail={"message": exc.message, "detail": exc.detail}
            ) from exc
        return {
            "reply": result.reply,
            "state": result.state,
            "state_changed": result.state_changed,
            "new_state": result.new_state,
            "triggered_action": result.triggered_action,
        }

    @post("/api/action")
    def post_action(self, req: ActionRequest):
        automaton = self.models_manager.get_active_automaton()
        from_state = automaton.get_current_state().key
        try:
            new_state = automaton.apply_action(req.action_name).target
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        automaton.log_transition(from_state, new_state, req.action_name, "manual")
        self.db.save_transition(
            from_state, req.action_name, new_state, self.models_manager.get_active_model_name()
        )
        return self._state_payload()

    @get("/api/autotracking")
    def get_autotracking(self):
        return {"enabled": self.chat_service.auto_tracking_enabled}

    @post("/api/autotracking")
    def post_autotracking(self, req: AutoTrackingRequest):
        self.chat_service.auto_tracking_enabled = req.enabled
        return {"enabled": self.chat_service.auto_tracking_enabled}

    @post("/api/triggers/preview")
    def post_triggers_preview(self, req: TriggersPreviewRequest):
        """For SignalsView's 'next triggerable action' panel: evaluates triggers
        for the current state against signal values the frontend already has
        (from GET /api/signals) — no AI call, never applies a transition."""
        return self.models_manager.get_active_automaton().preview_triggers(req.signals)

    @post("/api/reset")
    async def post_reset(self):
        """Manual reset — scoped to whichever model is currently active only:
        clears its own messages/signals/transitions, never other models'.
        State returns to that model's initial_state, not a blank slate shared
        across models."""
        async with self.chat_service.lock:
            model_name = self.models_manager.get_active_model_name()
            self.db.reset_model(model_name)
            automaton = self.models_manager.get_active_automaton()
            automaton.set_current_state(automaton.initial_state)
            self.chat_service.auto_tracking_enabled = True
            await self.chat_service.open_if_needed()
        return self._state_payload()

    @get("/api/models")
    def get_models(self):
        return self.models_manager.list_models()

    @put("/api/models/{model_name}/activate")
    async def activate_model(self, model_name: str):
        """Activates an already-present model, validated via the same
        AutomatonBuilder as boot/upload/delete. Idempotent: re-activating the
        active model still validates, but skips the swap + commit."""
        try:
            await self.models_manager.activate_model_idempotent(model_name, self._activate_model)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"success": True, "model_name": model_name}

    @get("/api/models/{model_name}")
    def get_model(self, model_name: str):
        """Downloads `model_name` as a zip — the read side of PUT
        /api/models/{model_name}, built so it round-trips back through PUT with
        no transformation. Not restricted to the active model."""
        try:
            content = self.models_manager.export_model_zip(model_name)
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
            return await self.models_manager.put_model(model_name, content, content_type, self._activate_model)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @delete("/api/models/{model_name}")
    async def delete_model(self, model_name: str):
        """Removes models/<model_name>/ from disk plus its conversation data —
        any model except "default" (PermissionError). If it was the active
        model, falls back to "default" via activate_model()."""
        try:
            await self.models_manager.delete_model(model_name, self._activate_model)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        except OSError as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc
        return {"success": True}

    def _state_payload(self) -> dict:
        return self.models_manager.get_active_automaton().get_current_state_payload()

    async def _activate_model(self, new_automaton: Automaton) -> None:
        """The commit callback for every model-lifecycle activation (switch/
        upload/delete-fallback) — no longer clears any data (see db.reset_model
        for that, used by DELETE and POST /api/reset instead). Restores
        whichever state the target model's own history last left it in, under
        chat_service.lock (shared with both chat transports)."""
        async with self.chat_service.lock:
            # models_manager already swapped _active_model_name for this
            # commit — set here, so it's current before open_if_needed()
            # (below) resolves "the active model" for its own is_empty() check.
            model_name = self.models_manager.get_active_model_name()
            self.db.set_active_model_name(model_name)
            new_automaton.set_current_state(self.db.get_current_state(new_automaton.initial_state, model_name))
            self.chat_service.auto_tracking_enabled = True
            await self.chat_service.open_if_needed()
