"""The authoring environment's own cross-screen surface: the boot
state every screen reads, the reference docs behind each "(?)" button,
and AI model selection as Manage services shows it.

What used to sit here about a *project* moved to
project/project_controller.py, which is core. What is left is the
platform itself, and a build without src/avance_platform/ answers none
of it — including GET /api/state, whose contributors (bus.POINT_API_STATE) then have
nobody collecting them, which is the correct outcome rather than an
empty payload nobody reads.
"""
from __future__ import annotations

from http import HTTPStatus
from pathlib import Path

from fastapi import HTTPException

from system import bus
from system.bus import OUTPUT_SPEECH, POINT_API_STATE

from turn.turn_service import TurnService
from avance_platform.platform_service import PlatformService
from project.project_service import ProjectService
from schemas import AiModelSelectionRequest

from controllers.base_controller import BaseController, get, post

# Slug -> filename under src/docs/ — a fixed allow-list, not a raw path
# built from the request, so get_doc can never be tricked into reading
# anything outside this directory.
DOC_FILES = {
    "project-specs": "PROJECT_SPECS.md",
    "metrics": "METRICS.md",
    "benchmark": "BENCHMARK.md",
    "markdown-guide": "MARKDOWN_GUIDE.md",
    "session-specs": "SESSION_SPECS.md",
    "skin-specs": "SKIN_SPECS.md",
}
DOCS_DIR = Path(__file__).resolve().parent.parent / "docs"


class PlatformController(BaseController):

    def __init__(
        self,
        turn_service: TurnService,
        project_service: ProjectService,
        platform_service: PlatformService,
    ) -> None:
        self.turn_service = turn_service
        self.project_service = project_service
        self.platform_service = platform_service

    @get("/api/docs/{name}")
    def get_doc(self, name: str):
        """Raw markdown content of one of src/docs/'s fixed set of
        reference docs — backs each "(?)" documentation button instead
        of duplicating it into the frontend bundle. Unknown `name` is a 404."""
        filename = DOC_FILES.get(name)
        if filename is None:
            raise HTTPException(status_code=HTTPStatus.NOT_FOUND, detail=f"Unknown doc '{name}'.")
        return {"content": (DOCS_DIR / filename).read_text(encoding="utf-8")}

    @get("/api/state")
    def get_state(self):
        """Also the frontend's boot/readiness ping — piggybacks
        talk_enabled here, plus whatever a skill contributes (see
        bus.POINT_API_STATE). No `-> StatePayload` annotation:
        with no active project/state the payload lacks those fields.
        talk_enabled here is the AND of two independent things: whether
        the talk skill is installed and configured at all, and whether
        the active project itself opted in (its own
        project.talk-enabled, defaulting true) — the chat toolbar's
        audio/spoken-text icons read this one combined flag rather than
        checking the project's own setting separately."""

        try:
            payload = self.platform_service.get_active_state_payload()
        except:
            payload = {}

        try:
            project_talk_enabled = self.project_service.get_active_automaton().talk_enabled
        except:
            project_talk_enabled = True

        payload["talk_enabled"] = bool(bus.handlers_for(OUTPUT_SPEECH)) and project_talk_enabled
        payload["input_token_budget_per_turn"] = self.turn_service.get_input_token_budget_per_turn()
        payload["total_token_budget_per_session"] = self.turn_service.get_total_token_budget_per_session()
        # Whatever else is running adds its own field: listen_enabled
        # comes from the Listen package when that package is there, and
        # simply isn't in the payload when it isn't.
        return bus.collect(POINT_API_STATE, payload)

    @get("/api/ai/models")
    def get_ai_models(self):
        """The ai-service provider roster (name/model/ui_label/ui_description),
        whether auto mode is on, and which model is in effect right now
        either way — for the chat toolbar's model menu."""
        return self.turn_service.get_ai_models_info()

    @post("/api/ai/models/selection")
    def post_ai_model_selection(self, req: AiModelSelectionRequest):
        """Sets which model generate()/generate_stream() use: `index:
        null` for auto (the cascade's fallback order), or `index` into
        GET /api/ai/models' `models` to pin one directly."""
        try:
            self.turn_service.select_ai_model(req.index)
        except ValueError as exc:
            raise HTTPException(status_code=HTTPStatus.BAD_REQUEST, detail=str(exc)) from exc
        return self.turn_service.get_ai_models_info()

    @get("/api/ai/models/test")
    def get_ai_test_models(self):
        return self.turn_service.get_test_ai_models_info()

    @post("/api/ai/models/test/selection")
    def post_ai_test_model_selection(self, req: AiModelSelectionRequest):
        try:
            self.turn_service.select_test_ai_model(req.index)
        except ValueError as exc:
            raise HTTPException(status_code=HTTPStatus.BAD_REQUEST, detail=str(exc)) from exc
        return self.turn_service.get_test_ai_models_info()
