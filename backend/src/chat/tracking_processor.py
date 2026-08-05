
from dataclasses import dataclass
from datetime import datetime
from http import HTTPStatus
import logging
from typing import AsyncIterator

from ai.ai_service import AiService
from ai.llm_provider import MetadataCallback
from automaton.automaton import Action, Automaton, State, StatePayload
from chat.env import Env
from chat.errors import ChatServiceError
from chat.priming import build_priming_messages
from chat.turn_protocol import TurnProtocol
from chat.turn_protocol_using_schema import TurnProtocolUsingSchema
from chat.turn_protocol_using_text_extraction import TurnProcotolUsingTextExtraction
from db.db import Db
from project.project_service import ProjectService
from tracking.evaluator import SignalEvaluator
from tracking.tracking_service import TrackingService
from .metadata_handler import MetadataHandler
from .session_manager import ChatSessionManager

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

@dataclass
class UserVariables:
	automaton: Automaton
	state: State
	project_name: str
	session_id: int
	message_id: int | None
	has_ai_started_conversation: bool

@dataclass
class OutVariables:
	reply: str
	messages: list[dict]
	tracking_id: int | None
	state: State
	action: Action | None

class TrackingProcessor(object):
	metadata_processor = MetadataHandler()
	user: UserVariables
	out: OutVariables

	def __init__(self, 
			  chat_service,
			  ai_service: AiService, 
			  project_service: ProjectService,
			  tracking_service: TrackingService,
			  env: Env,
			  db: Db, 
			  session_manager: ChatSessionManager):
		self.project_service = project_service
		self.ai_service = ai_service

		#FIXME 
		from .chat_service import ChatService

		self.chat_service : ChatService = chat_service 
		self.tracking_service = tracking_service
		self.env = env
		self.db = db
		self.session_manager = session_manager

	async def _get_ai_reply(self) -> OutVariables:
		raise NotImplementedError

	async def process(self, text: str | None, session_id, on_metadata: MetadataCallback | None = None) -> dict:

		def dummy_on_metadata(key: str, value: str) -> None:
			print(f'process.on_metadata: null call key {key} = {value}')

		self.metadata = Metadata(on_metadata or dummy_on_metadata, {}, {}, "", None)

		self.user = await self._save_user_message(text, session_id)
		self.out = await self._get_ai_reply()

		self.env.update(self.metadata.env)
		assistant_id = self.db.save_message("assistant", self.out.reply, self.user.session_id, audio_text=self.metadata.audio)

		if self.out.tracking_id is not None:
			self.db.link_signal_to_message(self.out.tracking_id, assistant_id)

		self.session_manager.touch_session(self.user.session_id, self.user.state.key)

		if self.user.has_ai_started_conversation and self.user.message_id:
			self.db.delete_message(self.user.message_id)

		return self._build_turn_response(assistant_id)

	async def _save_user_message(
		self, text: str | None, session_id: int | None
	) -> UserVariables:

		automaton, state = self.project_service.get_active_automaton_and_state()

		if not state.chat and text not in (None, "", "..."): # FIXME
			raise ChatServiceError(
				"This state doesn't accept messages; use an action instead.", status_code=HTTPStatus.CONFLICT
			)
	
		project_name = self.project_service.get_active_project_name()
		session = self.chat_service._require_active_session(session_id, project_name, state.key)
		resolved_session_id = session["id"]

		user_message_id = self.db.save_message("user", text or '...', resolved_session_id)

		return UserVariables(automaton, state, project_name, resolved_session_id, user_message_id, not text)

	def generate_reply(self, state: State, on_metadata: MetadataCallback,
	) -> AsyncIterator[str]:
		base_prompt, signal_definition, turn_attachments = self.__build_turn_prompt_parts(self.user.automaton, state)

		priming_messages = build_priming_messages(turn_attachments)
		since = self._history_cutoff(self.user.project_name, self.user.state)
		chat_history = priming_messages + self._strip_timestamps(
			self.db.get_messages(self.user.session_id, since=since)
		)

		protocol = self.build_turn_protocol()
		return protocol.generate_reply(
			base_prompt, signal_definition, self.env, chat_history, on_metadata
		)

	def build_turn_protocol(self) -> TurnProtocol:
		supports_schema = self.ai_service.is_provider_with_schema()
		has_to_evaluate_signals_before_ai_reply = self.user.automaton.autotracking_on_user_message
		Protocol = TurnProtocolUsingSchema if supports_schema else TurnProcotolUsingTextExtraction
		return Protocol(self.ai_service, has_to_evaluate_signals_before_ai_reply)    

	def __build_turn_prompt_parts(self, automaton: Automaton, state: State) -> tuple[str, str | None, list]:

		if state.fixed_message:
			logger.warning("Translating fixed_message for state '%s'.", state.key)
			return FIXED_MESSAGE_INSTRUCTIONS.format(fixed_message=state.fixed_message), None, []

		# which action fires from here.
		signal_names = automaton.triggerable_signal_names(state.key)
		signal_definition = self.tracking_service.get_definition(signal_names)
		base_prompt = f"{automaton.general_prompt}\n\n{state.contextual_prompt}"
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


	def _build_turn_response(self, assistant_message_id: int | None) -> dict:
		action = self.out.action
		return {
			"reply": self.out.messages,
			"user_message_id": self.user.message_id,
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

	async def _run_auto_tracking(self) -> tuple[Action | None, State, list[dict], int | None]:
		v = self.user
		action, new_state, signal_row_id = await self.tracking_service.run_auto_tracking(
			v.session_id, v.automaton, v.state, self.metadata.signals
		)
		if action is None:
			return None, v.state, [], signal_row_id

		messages = await self.chat_service._messages_for_transition(
			action, v.project_name, v.session_id, new_state, is_self_loop=(action.target == v.state.key)
		)
		return action, new_state, messages, signal_row_id


	def _FIXME_would_trigger_action(self) -> Action | None:

		signal_evaluator = SignalEvaluator()
		metrics = self.chat_service.metric_service

		state = self.user.state
		automaton = self.user.automaton
		signal_values = self.metadata.signals

		if not state.has_triggerable_actions:
			return None

		needed_signal_names = automaton.triggerable_signal_names(state.key)
		signal_values = signal_evaluator.validate(automaton, signal_values, needed_signal_names)

		evaluation_names = metrics.merge_if_referenced(automaton, state.key, signal_values)
		evaluation_names = self.env.merge_if_referenced(automaton, state.key, evaluation_names)
		triggered_action = automaton.evaluate_triggers_action(state.key, evaluation_names)

		return triggered_action
	
	def _FIXME_move_automaton(self) -> int:
		automaton = self.user.automaton
		state = self.user.state
		triggered_action = self.out.action.name if self.out.action else None
		signal_values = self.metadata.signals
		session_id = self.user.session_id

		if triggered_action is None:
			# No transition fired — just the evaluation itself is worth
			# keeping (see db.get_latest_signal_snapshot, Tracking.values).
			tracking_id = self.db.save_signal_snapshot(signal_values, session_id)
			return tracking_id

		action = automaton.move(state.key, triggered_action)

		# Always saved, self-loop or not — a fired trigger is a real event
		# worth a history entry either way. A self-loop just never bumps
		# history_cutoff's timestamp (see db.get_last_transition_timestamp).
		# The full evaluated values ride along on this same row (see
		# db.py's Tracking) instead of a separate snapshot row to link to.

		self.__FIXME_apply_action_env(action)
		tracking_id = self.db.save_transition(
			state.key,
			triggered_action,
			action.target,
			session_id,
			transition_log_level=automaton.get_state(action.target).transition_log_level,
			signal_values=signal_values,
		)

		return tracking_id


	def __FIXME_apply_action_env(self, action: Action) -> None:
		signal_values = self.metadata.signals
		metrics = self.chat_service.metric_service
		automaton = self.user.automaton

		if not action.env:
			return
		scope = {**signal_values, **metrics.calculate_values(), **self.env.to_dict()}
		updates = automaton.eval_action_env(action, scope)
		if updates:
			self.env.update_action_set(updates)

