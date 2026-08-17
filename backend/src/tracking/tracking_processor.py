
from dataclasses import dataclass, replace
from datetime import datetime
from http import HTTPStatus
import logging
from typing import AsyncIterator

from db.db import Db
from ai.ai_service import AiService
from ai.llm_provider import MetadataCallback
from metrics.metric_service import MetricService
from automaton.automaton import Action, Automaton, State, StatePayload

from .env import Env
from .priming import build_priming_messages
from .tracking_engine import DbTrackingSink, TrackingEngine
from .turn_protocol import TurnProtocol
from .turn_protocol_using_schema import TurnProtocolUsingSchema
from .turn_protocol_using_text_extraction import TurnProcotolUsingTextExtraction
from .metadata_handler import MetadataHandler
from .definitions import Signals
from .errors import TrackingServiceError

logger = logging.getLogger(__name__)

FIXED_MESSAGE_INSTRUCTIONS = (
	"You must reply with ONLY a translation of the fixed message below into "
	"the same language the user's last message is written in. Do not answer "
	"or react to what the user said, do not add or remove anything, and do "
	"not change its meaning or formatting — output just the translation.\n\n"
	"Fixed message:\n{fixed_message}"
)

@dataclass
class Metadata:
	on_metadata: MetadataCallback
	env: dict[str, str]
	signals: dict[str, float] 
	audio: str
	chunk: str | None = None

@dataclass(frozen=True, slots=True)
class UserVariables:
	automaton: Automaton
	state: State
	project_name: str
	session_id: int
	# Set by _save_user_message once the turn's own user-facing message
	# (real or placeholder) has been persisted — None beforehand.
	message_id: int | None = None
	# True when this turn has no real user text (an opening message, or
	# an action_prompt) — the AI is initiating, not replying. Literally
	# `not text`, computed once in _save_user_message.
	has_ai_started_conversation: bool = False

@dataclass
class OutVariables:
	reply: str
	messages: list[dict]
	tracking_id: int | None
	state: State
	action: Action | None
	# True once tracking_id's own row already carries the message id of
	# whichever message actually caused it (see TrackingProcessorAfterUserMessage
	# ._get_ai_reply, which knows self.user.message_id up front) — process()
	# must not then overwrite that link with the assistant's own message id.
	tracking_linked_to_message: bool = False

class TrackingProcessor(object):
	metadata_processor = MetadataHandler()
	user: UserVariables
	out: OutVariables

	def __init__(self, 
			  ai_service: AiService, 
			  metrics: MetricService,
			  env: Env,
			  db: Db, 
			  user_variables: UserVariables,
		):
		self.ai_service = ai_service

		self.metrics = metrics
		self.env = env
		self.db = db
		self.user = user_variables
		self._tracking_engine = TrackingEngine(DbTrackingSink(db), env, self.metrics)
		# Set per-turn by process() — appended to base_prompt after the
		# state's own contextual_prompt (see __build_turn_prompt_parts).
		self.extra_prompt: str | None = None

	async def _get_ai_reply(self) -> OutVariables:
		raise NotImplementedError

	def _save_user_message(self, text: str | None) -> None:
		"""Persists this turn's own user-facing message — the real text if
		there is one, a '...' placeholder otherwise (an opening message, or
		an action_prompt firing with no real user text) — and records
		enough on self.user (message_id, has_ai_started_conversation) for
		process() to know, once the AI's reply is in, whether that
		placeholder should be deleted again rather than kept as a fake
		user turn."""
		message_id = self.db.save_message("user", text or '...', self.user.session_id)
		self.user = replace(self.user, message_id=message_id, has_ai_started_conversation=not text)

	async def process(
		self, text: str | None, on_metadata: MetadataCallback | None = None, extra_prompt: str | None = None,
	) -> dict:

		state = self.user.state

		if not state.chat and text not in (None, "", "..."):
			raise TrackingServiceError(
				"This state doesn't accept messages; use an action instead.", status_code=HTTPStatus.CONFLICT
			)

		self.extra_prompt = extra_prompt
		self._save_user_message(text)

		def dummy_on_metadata(key: str, value: str) -> None:
			pass

		self.metadata = Metadata(on_metadata or dummy_on_metadata, {}, {}, "", None)

		self.out = await self._get_ai_reply()

		assistant_id = self.db.save_message("assistant", self.out.reply, self.user.session_id, audio_text=self.metadata.audio)
		# Linked to the assistant's own message right away — this turn's
		# reply is what actually reported these env values (see
		# MetadataHandler.parse_raw_env), so unlike self.out.tracking_id
		# (which may already be linked to an earlier, causing message —
		# see TrackingProcessorAfterUserMessage), this row has no such
		# earlier candidate to prefer.
		self.env.update(self.metadata.env, message_id=assistant_id)

		if self.out.tracking_id is not None and not self.out.tracking_linked_to_message:
			self.db.link_signal_to_message(self.out.tracking_id, assistant_id)

		user_message_id = self.user.message_id
		if self.user.has_ai_started_conversation and self.user.message_id:
			self.db.delete_message(self.user.message_id)
			user_message_id = None

		return self._build_turn_response(user_message_id, assistant_id)


	def generate_reply(self, state: State, on_metadata: MetadataCallback,
	) -> AsyncIterator[str]:
		base_prompt, signal_definition, turn_attachments = self.__build_turn_prompt_parts(self.user.automaton, state)
		chat_history = self._build_chat_history(turn_attachments)

		protocol = self.build_turn_protocol()
		return protocol.generate_reply(
			base_prompt, signal_definition, self.env, chat_history, on_metadata
		)

	def _build_base_prompt_and_history(self, state: State) -> tuple[str, list[dict]]:
		"""Same base_prompt/chat_history generate_reply itself builds for
		`state` — exposed (single-underscore, so subclasses in other
		modules can actually call it, unlike the name-mangled
		__build_turn_prompt_parts) for a caller that needs those two
		pieces without going through the full tag/signal_definition/env
		machinery generate_reply's own TurnProtocol.generate_reply call
		requires — see TrackingProcessorAfterUserMessage._get_ai_reply's
		own regeneration call, which asks for a narrower tag set via
		TurnProtocol.generate_reply_with_schema instead."""
		base_prompt, _signal_definition, turn_attachments = self.__build_turn_prompt_parts(self.user.automaton, state)
		return base_prompt, self._build_chat_history(turn_attachments)

	def _build_chat_history(self, turn_attachments: list) -> list[dict]:
		priming_messages = build_priming_messages(turn_attachments)
		since = self._history_cutoff(self.user.project_name, self.user.state)
		return priming_messages + self._strip_timestamps(
			self.db.get_messages(self.user.session_id, since=since)
		)

	def build_turn_protocol(self) -> TurnProtocol:
		supports_schema = self.ai_service.is_provider_with_schema()
		has_to_evaluate_signals_before_ai_reply = not self.user.automaton.autotracking_on_ai_message
		Protocol = TurnProtocolUsingSchema if supports_schema else TurnProcotolUsingTextExtraction
		return Protocol(self.ai_service, has_to_evaluate_signals_before_ai_reply)    

	def __build_turn_prompt_parts(self, automaton: Automaton, state: State) -> tuple[str, str | None, list]:

		if state.fixed_message:
			logger.warning("Translating fixed_message for state '%s'.", state.key)
			return FIXED_MESSAGE_INSTRUCTIONS.format(fixed_message=state.fixed_message), None, []

		# which action fires from here.
		signals = Signals(lambda: automaton, self.db)
		signal_names = automaton.triggerable_signal_names(state.key)
		signal_definition = signals.get_definition(signal_names)
		base_prompt = f"{automaton.general_prompt}\n\n{state.contextual_prompt}"
		if self.extra_prompt:
			base_prompt = f"{base_prompt}\n\n{self.extra_prompt}"
		return base_prompt, signal_definition, list(automaton.general_attachments.values()) + list(state.attachments.values())


	def _history_cutoff(self, project_name: str, state: State) -> datetime | None:
		"""Messages at or before this timestamp must be excluded from both
		the AI reply and auto-tracking's signal evaluation, per `state`'s
		history_cutoff. None means "no cutoff, use the full history"."""
		if not state.history_cutoff:
			return None
		return self.db.get_last_transition_timestamp(project_name)

	@staticmethod
	def _strip_timestamps(history: list[dict]) -> list[dict]:
		"""`LLMProvider.generate` only knows {role, content} — timestamps are
		kept in the persisted conversation for /api/signals, not sent to the
		model during normal chat."""
		return [{"role": m["role"], "content": m["content"]} for m in history]


	def _build_turn_response(self, user_message_id: int | None, assistant_message_id: int | None) -> dict:
		action = self.out.action
		return {
			"reply": self.out.messages,
			"user_message_id": user_message_id,
			"assistant_message_id": assistant_message_id,
			"state": self._current_state_payload(self.user.automaton, self.out.state),
			"state_changed": action is not None,
			"new_state": action.target if action else None,
			"triggered_action": action.name if action else None,
			"on-enter": action.on_enter if action else None,
			"ai_model": self.ai_service.get_models_info(),
			"session_id": self.user.session_id,
		}

	@staticmethod
	def _current_state_payload(automaton: Automaton, state: State) -> StatePayload:
		return automaton.get_state_payload(state)

