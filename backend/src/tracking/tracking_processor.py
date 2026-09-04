
from dataclasses import dataclass, replace
from http import HTTPStatus
from typing import AsyncIterator

from db.db import Db
from ai import AiService
from ai import MetadataCallback, content_to_text
from automaton.automaton import Action, Automaton, State, StatePayload
from logging_factory import LoggerFactory

from .env import Env
from .evaluation_scope import EvaluationScopeBuilder
from .priming import build_priming_messages
from .sources import SourceNamespace, ToolSet
from .tracking_engine import DbTrackingSink, TrackingEngine
from .turn_protocol import TurnProtocol
from .turn_protocol_using_schema import TurnProtocolUsingSchema
from .metadata_handler import MetadataHandler
from .definitions import Signals
from .errors import TrackingServiceError
from .fixed_project_context import FixedProjectContext

logger = LoggerFactory.get_logger(__name__)

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
	audio: str | None = None
	chunk: str | None = None
	# The bot's own reaction to the user's message this turn — unlike
	# audio/env/signals, this ends up persisted on the *user's* message
	# (see process() below), not the assistant's own new one.
	reaction: str | None = None
	input_tokens: int | None = None
	output_tokens: int | None = None

@dataclass(frozen=True, slots=True)
class UserVariables:
	automaton: Automaton
	state: State
	project_id: str
	session_id: int
	# Set by _save_user_message once the turn's own user-facing message
	# (real or placeholder) has been persisted — None beforehand.
	message_id: int | None = None
	# True when this turn has no real user text (an opening message) — the
	# AI is initiating, not replying. Literally `not text`, computed once
	# in _save_user_message.
	has_ai_started_conversation: bool = False

@dataclass
class OutVariables:
	reply: str
	messages: list[dict]
	tracking_id: int | None
	state: State
	action: Action | None
	# True once tracking_id's row already carries the message id of
	# whichever message actually caused it — process() must not then
	# overwrite that link with the assistant's own message id.
	tracking_linked_to_message: bool = False
	# True once this turn's signals are settled and won't change the state
	# again — either they were actually evaluated (whether or not that
	# triggered a transition), or a caller determined upfront that no
	# transition was ever possible. Gates whether buffered reply text is
	# safe to stream.
	signals_resolved: bool = False

class TrackingProcessor(object):
	metadata_processor = MetadataHandler()
	user: UserVariables
	out: OutVariables

	def __init__(self,
			  ai_service: AiService,
			  scope_builder: EvaluationScopeBuilder,
			  env: Env,
			  db: Db,
			  user_variables: UserVariables,
			  auto_tracking_enabled: bool = True,
			  talk_enabled: bool = True,
			  input_token_budget_per_turn: int | None = 16000,
		):
		self.ai_service = ai_service

		self.env = env
		self.db = db
		self.user = user_variables
		self.talk_enabled = talk_enabled
		self.input_token_budget_per_turn = input_token_budget_per_turn
		self._tracking_engine = TrackingEngine(DbTrackingSink(db), env, scope_builder, auto_tracking_enabled)

	async def _get_ai_reply(self) -> OutVariables:
		raise NotImplementedError

	def _save_user_message(self, text: str | None) -> None:
		"""Persists this turn's own user-facing message — the real text if
		there is one, a '...' placeholder otherwise — and records enough
		on self.user for process() to later delete that placeholder rather than keep it as a fake user turn."""
		message_id = self.db.save_message("user", text or '...', self.user.session_id)
		self.user = replace(self.user, message_id=message_id, has_ai_started_conversation=not text)

	async def process(self, text: str | None, on_metadata: MetadataCallback | None = None) -> dict:

		state = self.user.state

		if not state.chat and text not in (None, "", "..."):
			raise TrackingServiceError(
				"This state doesn't accept messages; use an action instead.", status_code=HTTPStatus.CONFLICT,
				code="state_not_chat",
			)

		self._save_user_message(text)

		def dummy_on_metadata(key: str, value: str) -> None:
			pass

		self.metadata = Metadata(on_metadata or dummy_on_metadata, {}, {})

		self.out = await self._get_ai_reply()

		logger.info(
			"process() got metadata.audio=%r for session %s", self.metadata.audio, self.user.session_id,
		)
		assistant_id = self.db.save_message(
			"assistant", self.out.reply, self.user.session_id,
			audio_text=self.metadata.audio, tokens=self.metadata.output_tokens,
		)
		# Linked to the assistant's own message right away — this turn's
		# reply is what actually reported these env values, unlike
		# self.out.tracking_id, which may already be linked to an earlier message.
		self.env.update(self.metadata.env, message_id=assistant_id)

		if self.out.tracking_id is not None and not self.out.tracking_linked_to_message:
			self.db.link_signal_to_message(self.out.tracking_id, assistant_id)

		user_message_id = self.user.message_id
		if self.user.has_ai_started_conversation and self.user.message_id:
			self.db.delete_message(self.user.message_id)
			user_message_id = None

		# The bot's reaction is *to* the user's own message this turn, so it
		# lands there, not on the assistant's new one — skipped for an
		# AI-started turn, whose "user" message was just deleted above,
		# same guard build_turn_response's own user_message_id uses.
		if self.metadata.reaction and user_message_id is not None:
			self.db.set_message_reaction(user_message_id, self.metadata.reaction)

		if self.metadata.input_tokens is not None and user_message_id is not None:
			self.db.set_message_tokens(user_message_id, self.metadata.input_tokens)

		return self._build_turn_response(user_message_id, assistant_id)


	def generate_reply(self, state: State, on_metadata: MetadataCallback,
	) -> AsyncIterator[str]:
		base_prompt, signal_definition, reaction_definition, turn_attachments = self.__build_turn_prompt_parts(self.user.automaton, state)
		chat_history = self._build_chat_history(turn_attachments)

		protocol = self.build_turn_protocol()
		return protocol.generate_reply(
			base_prompt, signal_definition, self.env, chat_history, on_metadata,
			reaction_definition=reaction_definition, tool_set=self.build_tool_set(state),
		)

	def build_tool_set(self, state: State) -> ToolSet | None:
		"""`state`'s own tool catalog (see automaton.State.tools) — None
		(no tools: declared) all the way down to a request identical to
		before tool-calling existed. Resolved fresh per call against this
		turn's own automaton/session, same SourceNamespace shape a
		source.<name> trigger/env: reference already uses."""
		if not state.tools:
			return None
		return SourceNamespace(self.db, self.user.automaton, self.user.session_id).tool_set(state.tools)

	def _build_base_prompt_and_history(self, state: State) -> tuple[str, list[dict]]:
		"""Same base_prompt/chat_history generate_reply itself builds for
		`state` — exposed single-underscore (rather than name-mangled) so
		a caller can get those two pieces without the full TurnProtocol.generate_reply machinery."""
		base_prompt, _signal_definition, _reaction_definition, turn_attachments = self.__build_turn_prompt_parts(self.user.automaton, state)
		return base_prompt, self._build_chat_history(turn_attachments)

	def _build_chat_history(self, turn_attachments: list) -> list[dict]:
		priming_messages = build_priming_messages(turn_attachments)
		since = self.db.history_cutoff_for_session(self.user.session_id, self.user.state.history_cutoff)
		return priming_messages + self._strip_timestamps(
			self.db.get_turn_history(self.user.session_id, since, self.input_token_budget_per_turn)
		)

	def build_turn_protocol(self) -> TurnProtocol:
		has_to_evaluate_signals_before_ai_reply = not self.user.automaton.autotracking_on_ai_message
		talk_enabled = self.talk_enabled and self.user.automaton.talk_enabled
		logger.info(
			"build_turn_protocol talk_enabled: project=%r revision=%s session=%s system_talk_enabled=%s "
			"automaton_talk_enabled=%s -> %s",
			self.user.project_id, self.user.automaton.revision, self.user.session_id, self.talk_enabled,
			self.user.automaton.talk_enabled, talk_enabled,
		)
		# The "before" strategy evaluates signals against *this turn's own
		# user message — an AI-started turn has none (just the '...'
		# placeholder), so there's nothing real to evaluate yet. The
		# "after" strategy evaluates the AI's own freshly-generated reply
		# instead, which is real content even on an AI-started turn, so it
		# isn't affected.
		evaluate_signals = not (has_to_evaluate_signals_before_ai_reply and self.user.has_ai_started_conversation)
		return TurnProtocolUsingSchema(
			self.ai_service, has_to_evaluate_signals_before_ai_reply, evaluate_signals=evaluate_signals,
			reactions_enabled=self.user.automaton.reactions_enabled_for(self.user.state),
			talk_enabled=talk_enabled,
		)

	def __build_turn_prompt_parts(self, automaton: Automaton, state: State) -> tuple[str, str | None, str | None, list]:

		if state.fixed_message:
			logger.warning("Translating fixed_message for state '%s'.", state.key)
			return FIXED_MESSAGE_INSTRUCTIONS.format(fixed_message=state.fixed_message), None, None, []

		# which action fires from here.
		# Pinned to THIS turn's own already-resolved automaton (never
		# whatever project happens to be "active" right now, which need
		# not be the same one this session actually belongs to).
		signals = Signals(FixedProjectContext(automaton), self.db)
		signal_names = automaton.triggerable_signal_names(state.key)
		signal_definition = signals.get_definition(signal_names)
		# Unlike signal_definition, never filtered down to a subset — the
		# bot's own reaction access is all-or-nothing per state (see
		# State.reactions_enabled), never a partial vocabulary.
		reaction_definition = self._build_reaction_definition(automaton) if automaton.reactions_enabled_for(state) else None
		base_prompt = f"{automaton.general_prompt}\n\n{state.contextual_prompt}"
		return (
			base_prompt, signal_definition, reaction_definition,
			list(automaton.general_attachments.values()) + list(state.attachments.values()),
		)

	@staticmethod
	def _build_reaction_definition(automaton: Automaton) -> str | None:
		if not automaton.reactions:
			return None
		return "- Definition of reactions:\n" + "\n\n".join(
			f'\t- Reaction "{r.name}":\n{r.definition}' for r in automaton.reactions
		)


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
			# The bot's own reaction to the user's message this turn (see
			# process()'s own persistence above) — carried here too so the
			# frontend can apply it live, without waiting for a full
			# messages refetch to notice the DB write.
			"user_message_reaction": self.metadata.reaction if user_message_id is not None else None,
			"state": self._current_state_payload(self.user.automaton, self.out.state),
			"state_changed": action is not None,
			"new_state": action.target if action else None,
			"triggered_action": action.name if action else None,
			"ai_model": self.ai_service.get_models_info(),
			"session_id": self.user.session_id,
		}

	@staticmethod
	def _current_state_payload(automaton: Automaton, state: State) -> StatePayload:
		return automaton.get_state_payload(state)


def estimate_state_prompt(ai_service: AiService, automaton: Automaton, state: State) -> str:
	"""The system_prompt TrackingProcessor.generate_reply would actually
	send for `state`, plus a synthetic one-turn history standing in for a
	real conversation — a single '...' placeholder user message, preceded
	by the state's own attachments (see build_priming_messages). Renders
	with no live session/Db needed, for ProjectInspector.get_state_input_tokens'
	own per-state input-token estimate."""
	if state.fixed_message:
		base_prompt = FIXED_MESSAGE_INSTRUCTIONS.format(fixed_message=state.fixed_message)
		signal_definition = None
		reaction_definition = None
		turn_attachments: list = []
	else:
		signals = Signals(FixedProjectContext(automaton), None)
		signal_definition = signals.get_definition(automaton.triggerable_signal_names(state.key))
		reaction_definition = (
			TrackingProcessor._build_reaction_definition(automaton) if automaton.reactions_enabled_for(state) else None
		)
		base_prompt = f"{automaton.general_prompt}\n\n{state.contextual_prompt}"
		turn_attachments = list(automaton.general_attachments.values()) + list(state.attachments.values())

	env = Env(stored={key.name: key.value for key in automaton.env_keys})
	has_to_evaluate_signals_before_ai_reply = not automaton.autotracking_on_ai_message
	protocol = TurnProtocolUsingSchema(
		ai_service, has_to_evaluate_signals_before_ai_reply,
		reactions_enabled=automaton.reactions_enabled_for(state), talk_enabled=automaton.talk_enabled,
	)
	system_prompt = protocol.build_final_prompt(base_prompt, signal_definition, env, reaction_definition)

	history_parts = [content_to_text(message["content"]) for message in build_priming_messages(turn_attachments)]
	history_parts.append("...")
	return "\n\n".join([system_prompt, *history_parts])

