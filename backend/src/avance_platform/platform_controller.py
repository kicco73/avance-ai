"""The authoring environment's own cross-screen surface: the boot
state every screen reads, the reference docs behind each "(?)" button,
and AI model selection as Manage services shows it.

What used to sit here about a *project* moved to
project/project_controller.py, which is core. What is left is the
platform itself, and a build without src/avance_platform/ answers none
of it — including GET /api/skills/platform/state, whose contributors (bus.POINT_API_STATE) then have
nobody collecting them, which is the correct outcome rather than an
empty payload nobody reads.
"""
from __future__ import annotations

from http import HTTPStatus

from fastapi import HTTPException


from turn.turn_service import TurnService
from avance_platform.doc_catalog import DOCS
from schemas import AiModelSelectionRequest

from controllers.base_controller import BaseController, get, post



class PlatformController(BaseController):

    def __init__(self, turn_service: TurnService) -> None:
        self.turn_service = turn_service

    @get("/api/skills/platform/docs/{name}")
    def get_doc(self, name: str):
        """Raw markdown content of one of src/docs/'s fixed set of
        reference docs — backs each "(?)" documentation button instead
        of duplicating it into the frontend bundle. Unknown `name` is a 404."""
        doc = DOCS.get(name)
        if doc is None:
            raise HTTPException(status_code=HTTPStatus.NOT_FOUND, detail=f"Unknown doc '{name}'.")
        return {"content": doc.render()}

    @get("/api/skills/platform/ai/models")
    def get_ai_models(self):
        """The ai-service provider roster (name/model/ui_label/ui_description),
        whether auto mode is on, and which model is in effect right now
        either way — for the chat toolbar's model menu."""
        return self.turn_service.get_ai_models_info()

    @post("/api/skills/platform/ai/models/selection")
    def post_ai_model_selection(self, req: AiModelSelectionRequest):
        """Sets which model generate()/generate_stream() use: `index:
        null` for auto (the cascade's fallback order), or `index` into
        GET /api/skills/platform/ai/models' `models` to pin one directly."""
        try:
            self.turn_service.select_ai_model(req.index)
        except ValueError as exc:
            raise HTTPException(status_code=HTTPStatus.BAD_REQUEST, detail=str(exc)) from exc
        return self.turn_service.get_ai_models_info()

    @get("/api/skills/platform/ai/models/test")
    def get_ai_test_models(self):
        return self.turn_service.get_test_ai_models_info()

    @post("/api/skills/platform/ai/models/test/selection")
    def post_ai_test_model_selection(self, req: AiModelSelectionRequest):
        try:
            self.turn_service.select_test_ai_model(req.index)
        except ValueError as exc:
            raise HTTPException(status_code=HTTPStatus.BAD_REQUEST, detail=str(exc)) from exc
        return self.turn_service.get_test_ai_models_info()
