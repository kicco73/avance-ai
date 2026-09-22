from __future__ import annotations

from dataclasses import asdict
from pathlib import Path

from ai import config as ai_config
from ai.ai_service import AiService
from ai.ai_input_processor import AiInputProcessor
from ai.ai_talker import AiTalker
from ai.stream_deadline import StreamDeadline
from ai import turn_kit
from config import ReplyDeadline
from system import bus
from system.bus import POINT_CORE_SERVICES, POINT_INPUT_PROCESSORS
from system.logging_factory import LoggerFactory
from system.skills import Skill

logger = LoggerFactory.get_logger(__name__)

# FIXME: 16000 mirrors turn-service.input-token-budget-per-turn's own default
_DEFAULT_INPUT_TOKEN_BUDGET_PER_TURN = 16000


def _stream_deadline(raw: dict) -> StreamDeadline:
    section = (raw or {}).get("turn-service") or {}
    default = ReplyDeadline()
    return StreamDeadline(**{
        field: float(section.get(field.replace("_", "-"), value)) for field, value in asdict(default).items()
    })


def _input_token_budget_per_turn(raw: dict) -> int:
    section = (raw or {}).get("turn-service") or {}
    return int(section.get("input-token-budget-per-turn", _DEFAULT_INPUT_TOKEN_BUDGET_PER_TURN))


class AiSkill(Skill):

    key = "ai"
    ui_label = "AI"
    ui_description = "Generates replies with a language model."
    project_declarable = True

    def requirements(self) -> list[str]:
        return ["anthropic>=0.40", "google-genai>=1.0", "openai>=2.45.0"]

    def __init__(self) -> None:
        self._configs = None
        self._live: AiService | None = None
        self._test: AiService | None = None

    def start_service(self, raw: dict, path: Path) -> None:
        self._configs = ai_config.parse(raw, path)
        if self._configs is None:
            logger.info(
                "ai-service is not configured — turns need a human operator to answer, and "
                "task.prompt/task.websearch return ''."
            )
            return
        db = bus.collect(POINT_CORE_SERVICES, {}).get("db")
        deadline = _stream_deadline(raw)
        budget = _input_token_budget_per_turn(raw)
        self._live = AiService.for_live(self._configs, db=db, input_token_budget_per_turn=budget, deadline=deadline)
        self._test = AiService.for_test(self._configs, db=db, input_token_budget_per_turn=budget, deadline=deadline)
        bus.contribute(POINT_CORE_SERVICES, lambda registry: registry.update({
            "ai_live_service": self._live, "ai_test_service": self._test,
            "ai_talker": AiTalker(ai_service=self._live),
            "ai_turn_kit": turn_kit.DEFAULT,
        }))
        bus.contribute(POINT_INPUT_PROCESSORS, lambda registry: registry.update({"ai": AiInputProcessor}))
        logger.info("ai-service started.")

    def describe_section(self, snapshot: dict) -> None:
        snapshot[self.key] = self.section(ai_config.public_fields(self._configs))

    def required_by(self, automaton, sources: dict[str, str | bytes]) -> bool:
        """A project with any state declaring `input-processor: ai` cannot
        run in a build without this package: TurnService.processor_for
        would find nobody registered for it and raise where the state
        expects the model to answer."""
        return any(state.input_processor == "ai" for state in automaton.states.values())
