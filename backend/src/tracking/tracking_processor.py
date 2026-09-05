
from dataclasses import dataclass, field, replace
from http import HTTPStatus
from typing import Any, AsyncIterator

from chat.errors import ChatServiceError
from db.db import Db
from ai import AiService
from ai import MetadataCallback, ToolAbortDecider, content_to_text
from automaton.automaton import Action, Automaton, State, StatePayload
from logging_factory import LoggerFactory
from session import Session

from .env import Env
from .env_prompt_block import EnvPromptBlock
from .evaluation_scope import EvaluationScopeBuilder
from .channels import (
	AudioChannel, MemoryChannel, MetadataChannel, ReactionChannel, SignalsChannel, TextChannel, TranslateChannel,
)
from .priming import build_priming_messages
from .sources import SourceNamespace, ToolSet
from .tracking_engine import DbTrackingSink, TrackingEngine
from .turn_protocol_using_schema import TurnProtocolUsingSchema
from .turn_size_estimate import TurnSizeEstimate, estimate_turn_request
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
	# The reply's own `memory` field, parsed — the model's free-form notes
	# delta (see Env.update). The automaton's env is never reported here:
	# the model writes it only through an avance:env source's `update`
	# tool, mid-generation (see tracking.sources.avance_env).
	memory: dict[str, str]
	signals: dict[str, float]
	audio: str | None = None
	chunk: str | None = None
	# The bot's own reaction to the user's message this turn — unlike
	# audio/memory/signals, this ends up persisted on the *user's* message
	# (see process() below), not the assistant's own new one.
	reaction: str | None = None
	input_tokens: int | None = None
	output_tokens: int | None = None
	# One {name, arguments, result, summary_text} entry per tool call this
	# turn's own AI generation made (see AiService's own tool-call loop and
	# TrackingProcessor.on_receiving_metadata's own 'tool_result' branch),
	# in the order they ran — empty for a turn with neither
	# ai-may-query-sources nor ai-must-query-sources declared, or one that
	# never actually called any.
	tool_calls: list[dict] = field(default_factory=list)
	# {action name: translated button label} for this turn's own resulting
	# state — only ever non-empty when a TranslateChannel was actually
	# appended to that turn's own channel list (see
	# TrackingProcessor._button_labels_to_translate), empty otherwise.
	button_translations: dict[str, str] = field(default_factory=dict)

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
	# True once this turn's trigger evaluation has run and the state won't
	# change again — on the model's own reported signals, or on the empty
	# signals set when nothing signal-backed was requested (see
	# TrackingProcessor._resolve_signals). Gates whether buffered reply
	# text is safe to stream.
	signals_resolved: bool = False

class TrackingProcessor(object):
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
		self.auto_tracking_enabled = auto_tracking_enabled
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
		# reply is what actually reported these memory values, unlike
		# self.out.tracking_id, which may already be linked to an earlier message.
		self.env.update(self.metadata.memory, message_id=assistant_id, declared_keys=self.user.automaton.declared_env_key_names())

		if self.metadata.tool_calls:
			self.db.record_tool_calls(self.user.session_id, self.metadata.tool_calls, message_id=assistant_id)
		# Binds any avance:env `update` tool call this turn made to the
		# assistant's own message, same reasoning as record_tool_calls
		# above — a no-op when nothing wrote through that tool this turn.
		self.db.link_tool_env_writes_to_message(self.user.session_id, assistant_id)

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

	def on_receiving_metadata(self, key: str, value: Any) -> None:
		"""Shared by every generate call this processor (or a subclass)
		makes — values arrive here already decoded through their own
		MetadataChannel (see TurnProtocolUsingSchema.generate_reply), so
		this only ever stores them or drives a real side effect, never
		parses raw model output itself. 'signals' is the one branch with
		a genuine side effect (a fresh signals value may trigger an
		automaton transition) — safe to fire unconditionally even on a
		call whose own channel list never includes SignalsChannel (the
		regeneration call in TrackingProcessorAfterUserMessage): a
		schema-constrained provider structurally can't emit a field
		outside the schema it was given, so this branch is simply
		unreachable there, same reasoning already relied on for
		'reaction'/'memory'/'audio' being a strict superset of what any one
		call actually requests."""
		rv = value
		if key == 'signals':
			rv = value
			self._resolve_signals(value)
		elif key == 'memory':
			rv = self.metadata.memory = value
		elif key == 'audio':
			rv = self.metadata.audio = value
		elif key == 'reaction':
			rv = self.metadata.reaction = value
		elif key == 'translations':
			rv = self.metadata.button_translations = value
		elif key == 'input_tokens':
			rv = self.metadata.input_tokens = (self.metadata.input_tokens or 0) + value
		elif key == 'output_tokens':
			rv = self.metadata.output_tokens = value
		elif key == 'tool_result':
			self.metadata.tool_calls.append(value)
		self.metadata.on_metadata(key, rv)

	def _resolve_signals(self, signal_values: dict[str, float]) -> None:
		"""The one trigger-evaluation pass of a turn: `signal_values` are
		the model's own reported signals when they were requested, or the
		empty set when they weren't (see _evaluate_signals_for) — a state
		whose triggers reference only metric.*/env.*/source.* is evaluated
		every chat turn all the same, exactly as one with signal-backed
		triggers; a signal-backed trigger evaluated against the empty set
		simply short-circuits to false (see Automaton._eval_trigger)."""
		self.metadata.signals = signal_values
		self.out.action = self._tracking_engine.evaluate_triggered_action(
			self.user.automaton, self.user.state, self.metadata.signals, session_id=self.user.session_id,
		)
		if self.out.action:
			self.out.state = self.user.automaton.get_state(self.out.action.target)
		self.out.signals_resolved = True

	def _records_evaluation(self) -> bool:
		"""Whether this turn's trigger evaluation leaves a Tracking row: a
		fired transition always does; an evaluation with no transition
		only when the model actually reported signals worth a snapshot
		(see TrackingEngine.apply_transition) — one against the empty set
		has nothing new to record."""
		return bool(self.metadata.signals) or self.out.action is not None

	def generate_reply(self, state: State, on_metadata: MetadataCallback, tool_abort: "ToolAbortDecider | None" = None,
	) -> AsyncIterator[str]:
		base_prompt, signal_definition, reaction_definition, turn_attachments = self.__build_turn_prompt_parts(self.user.automaton, state)
		channels = self.build_turn_channels(state, base_prompt, signal_definition, reaction_definition)
		env_block = EnvPromptBlock.for_state(self.env, self.user.automaton, state)
		remaining_history_budget = self._enforce_input_budget(
			base_prompt, signal_definition, reaction_definition, turn_attachments, channels, env_block,
		)
		chat_history = self._build_chat_history(turn_attachments, remaining_history_budget)

		return TurnProtocolUsingSchema(self.ai_service).generate_reply(
			channels, chat_history, on_metadata,
			tool_set=self.build_tool_set(state), force_required_tools=self.force_required_tools_for(state),
			env_block=env_block.text() if env_block else None, tool_abort=tool_abort,
		)

	def should_abort_tools(self) -> bool:
		"""AiService.generate_stream_with_metadata's own tool_abort check —
		whether a tool-call round arriving after this turn's own signals
		already resolved into a transition should be discarded rather than
		run. Once the automaton has moved, whatever reply that round would
		produce is thrown away and regenerated from scratch in the new
		state (see TrackingProcessorAfterUserMessage's own `transitioned`
		branch), so resolving the tool call first would only ever be
		wasted work."""
		return self.out.signals_resolved and self.user.state != self.out.state

	def _enforce_input_budget(
		self, base_prompt: str, signal_definition: str | None, reaction_definition: str | None,
		turn_attachments: list, channels: list[MetadataChannel] | None = None,
		env_block: "EnvPromptBlock | None" = None,
	) -> int | None:
		budget = self.input_token_budget_per_turn
		if budget is None:
			return None
		# Every channel's own fixed preamble + SCHEMA_ORDER_PROMPT are real
		# prompt bytes the model actually sees, on top of base_prompt/
		# signal_definition/reaction_definition — omitted here, the
		# estimate used to under-count every request by that much.
		schema_overhead = TurnProtocolUsingSchema.schema_overhead_text(channels) if channels is not None else ""
		estimate = estimate_turn_request(
			base_prompt, signal_definition, reaction_definition, self.env, turn_attachments,
			schema_overhead=schema_overhead, env_block=env_block,
		)
		if estimate.total_tokens > budget:
			self._reject_over_budget(estimate, budget)
		return budget - estimate.total_tokens

	def _reject_over_budget(self, estimate: TurnSizeEstimate, budget: int) -> None:
		heaviest = ", ".join(
			f"{entry.label} ({entry.kind}, ~{entry.tokens} tok)" for entry in estimate.heaviest(3)
		)
		message = (
			f"Session {self.user.session_id} (project '{self.user.project_id}'): this turn's own system "
			f"prompt alone is ~{estimate.total_tokens} tokens, over the {budget}-token "
			f"input-token-budget-per-turn cap. Heaviest: {heaviest}."
		)
		logger.warning(message)
		self.db.save_system_warning(Session().user, self.user.project_id, "input_budget_exceeded", message)
		raise ChatServiceError(
			f"This turn's own system prompt alone is ~{estimate.total_tokens} tokens, over the "
			f"{budget}-token cap.",
			status_code=HTTPStatus.REQUEST_ENTITY_TOO_LARGE, code="input_budget_exceeded",
		)

	def build_tool_set(self, state: State) -> ToolSet | None:
		"""`state`'s own tool catalog (see automaton.State.
		ai_may_read_sources/ai_must_read_sources/ai_may_write_sources) —
		None (none of the three declared) all the way down to a request
		identical to before tool-calling existed. Resolved fresh per call
		against this turn's own automaton/session/env, same SourceNamespace
		shape a source.<name> trigger/env: reference already uses — `env`
		given here too, so a write source (e.g. avance:env's `update`) can
		actually persist through it mid-generation."""
		if not state.ai_source_names:
			return None
		return SourceNamespace(self.db, self.user.automaton, self.user.session_id, env=self.env).tool_set(
			state.ai_may_read_sources, state.ai_must_read_sources, state.ai_may_write_sources,
		)

	def force_required_tools_for(self, state: State) -> bool:
		"""Whether this turn is the first one generated since `state` was
		last entered — the one turn ai-must-read-sources actually forces a
		call on (see AiService.generate_stream_with_metadata's own
		force_required_tools). Decided here, from Tracking/Message history,
		never left to the model: a session with no assistant message yet
		since the Tracking row that landed it on `state` (including the
		project's own bootstrap into its initial state, and a self-loop
		action re-entering the same state) still owes that first call."""
		if not state.ai_must_read_sources:
			return False
		since = self.db.get_last_entry_timestamp_for_session(self.user.session_id, state.key)
		if since is None:
			return True
		return not self.db.has_assistant_message_since(self.user.session_id, since)

	def _build_base_prompt_and_history(self, state: State) -> tuple[str, list[dict], "EnvPromptBlock | None"]:
		"""Same base_prompt/chat_history/env_block the transition-
		regeneration path (TrackingProcessorAfterUserMessage) actually
		sends for `state` — exposed single-underscore (rather than
		name-mangled) so that caller can get those pieces on their own,
		sized against the same (audio, text, memory, [translations])
		channel set build_regeneration_channels itself builds for the
		real call, not the full gated set generate_reply would use.
		`env_block` is handed back rather than recomputed by the caller —
		EnvPromptBlock.for_state reads through self.env/automaton, no
		reason to do that twice for one regeneration call."""
		base_prompt, signal_definition, reaction_definition, turn_attachments = self.__build_turn_prompt_parts(self.user.automaton, state)
		channels = self.build_regeneration_channels(state, base_prompt)
		env_block = EnvPromptBlock.for_state(self.env, self.user.automaton, state)
		remaining_history_budget = self._enforce_input_budget(
			base_prompt, signal_definition, reaction_definition, turn_attachments, channels, env_block,
		)
		return base_prompt, self._build_chat_history(turn_attachments, remaining_history_budget), env_block

	def _build_chat_history(self, turn_attachments: list, token_budget: int | None) -> list[dict]:
		priming_messages = build_priming_messages(turn_attachments)
		since = self.db.history_cutoff_for_session(self.user.session_id, self.user.state.history_cutoff)
		return priming_messages + self._strip_timestamps(
			self.db.get_turn_history(self.user.session_id, since, token_budget)
		)

	def build_turn_channels(
		self, state: State, base_prompt: str, signal_definition: str | None, reaction_definition: str | None,
	) -> list[MetadataChannel]:
		"""The full, gated channel list for a reply generated in `state` —
		whichever state this turn's own reply is actually about to be
		generated in. Every caller except the regenerate-after-transition
		path (TrackingProcessorAfterUserMessage) passes self.user.state
		(the turn's own pre-transition state); that one path must pass
		its own self.out.state instead: the gating below is about what's
		triggerable/translatable from THAT state, not the one the turn
		started in."""
		has_to_evaluate_signals_before_ai_reply = not self.user.automaton.autotracking_on_ai_message
		talk_enabled = self.talk_enabled and self.user.automaton.talk_enabled
		logger.info(
			"build_turn_channels talk_enabled: project=%r revision=%s session=%s system_talk_enabled=%s "
			"automaton_talk_enabled=%s -> %s",
			self.user.project_id, self.user.automaton.revision, self.user.session_id, self.talk_enabled,
			self.user.automaton.talk_enabled, talk_enabled,
		)
		reactions_enabled = self.user.automaton.reactions_enabled_for(self.user.state)

		channels = self._order_channels(
			has_to_evaluate_signals_before_ai_reply,
			signals=SignalsChannel(signal_definition) if self._evaluate_signals_for(state) else None,
			reaction=ReactionChannel(reaction_definition) if reactions_enabled else None,
			audio=AudioChannel() if talk_enabled else None,
			text=TextChannel(base_prompt),
			memory=MemoryChannel(self.env),
		)
		self._append_translate_channel(channels, state)
		return channels

	def _evaluate_signals_for(self, state: State) -> bool:
		"""Whether a reply generated in `state` should even ask the model
		for 'signals' — see build_turn_channels' own docstring on the
		"before"/"after" strategies; pointless when nothing in `state`
		could trigger from them (no definition in the prompt, no
		'signals' field in the schema). This gates the *request* only,
		never the trigger evaluation itself: a turn with a real user
		message that asks for nothing still runs _resolve_signals against
		the empty set, so a trigger referencing only metric.*/env.*/
		source.* keeps firing — except at the opening turn (see
		TrackingProcessorAfterUserMessage._get_ai_reply's own has_ai_
		started_conversation branch), which skips that evaluation outright
		rather than let the automaton's own AI-generated opener alone fire
		a transition nothing in the conversation asked for. Exposed on
		its own (not just inlined in build_turn_channels) so a caller can
		know this upfront without building the full channel list — see
		TrackingProcessorAfterUserMessage's own upfront resolution."""
		has_to_evaluate_signals_before_ai_reply = not self.user.automaton.autotracking_on_ai_message
		return (
			not (has_to_evaluate_signals_before_ai_reply and self.user.has_ai_started_conversation)
		) and bool(self.user.automaton.triggerable_signal_names(state.key))

	def build_regeneration_channels(self, state: State, base_prompt: str) -> list[MetadataChannel]:
		"""The fixed (audio, text, memory) set the transition-regeneration
		call has always sent — signals are already known from the first
		call and must not be re-requested; talk_enabled/reaction gating
		never applied here either (pre-existing behavior, preserved
		as-is). `state` is the real post-transition state — the one call
		site that actually knows it at prompt-build time — so this is
		where button translation is genuinely correct after a transition."""
		channels: list[MetadataChannel] = [AudioChannel(), TextChannel(base_prompt), MemoryChannel(self.env)]
		self._append_translate_channel(channels, state)
		return channels

	def _append_translate_channel(self, channels: list[MetadataChannel], state: State) -> None:
		originals = self._button_labels_to_translate(state, self.auto_tracking_enabled)
		if originals:
			channels.append(TranslateChannel(originals))

	@staticmethod
	def _button_labels_to_translate(state: State, auto_tracking_enabled: bool) -> dict[str, str]:
		"""{action name: original ui_button text} for every action `state`
		would show as a manual button — same filter as
		automaton.manual_actions_for, over live Action objects instead of
		serialized ActionPayload dicts, plus requiring a non-empty
		ui_button (nothing to translate otherwise)."""
		return {
			a.name: a.ui_button for a in state.actions
			if (a.trigger is None or not auto_tracking_enabled) and a.ui_button
		}

	@staticmethod
	def _order_channels(
		evaluate_signals_first: bool, *,
		signals: MetadataChannel | None, reaction: MetadataChannel | None,
		audio: MetadataChannel | None, text: MetadataChannel, memory: MetadataChannel,
	) -> list[MetadataChannel]:
		"""'before' -> signals, reaction, audio, text, memory; 'after' ->
		audio, text, signals, reaction, memory — exactly the ordering a
		turn has always used, now over channel objects (any of
		signals/reaction/audio may be None, meaning "not active this
		turn")."""
		signal_channels = [signals] if signals is not None else []
		reaction_channels = [reaction] if reaction is not None else []
		audio_channels = [audio] if audio is not None else []
		if evaluate_signals_first:
			return [*signal_channels, *reaction_channels, *audio_channels, text, memory]
		return [*audio_channels, text, *signal_channels, *reaction_channels, memory]

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
		# The turn's own persisted assistant message, same shape
		# apply_manual_action's own "reply" already sends (see
		# ChatService._messages_for_transition) — lets a live SSE/WS turn's
		# frontend reconcile its streaming bubble against the persisted
		# row on `done`, instead of trusting the stream to have delivered
		# every chunk. Empty exactly when there's no such message
		# (assistant_message_id is always set by process()'s own
		# save_message call today, but this stays defensive against a
		# future caller that doesn't).
		reply = [self.db.get_message(assistant_message_id)] if assistant_message_id is not None else []
		return {
			"reply": reply,
			"user_message_id": user_message_id,
			"assistant_message_id": assistant_message_id,
			# The bot's own reaction to the user's message this turn (see
			# process()'s own persistence above) — carried here too so the
			# frontend can apply it live, without waiting for a full
			# messages refetch to notice the DB write.
			"user_message_reaction": self.metadata.reaction if user_message_id is not None else None,
			"state": self._current_state_payload(self.user.automaton, self.out.state, self.metadata.button_translations),
			"state_changed": action is not None,
			"new_state": action.target if action else None,
			"triggered_action": action.name if action else None,
			"ai_model": self.ai_service.get_models_info(),
			"session_id": self.user.session_id,
		}

	@staticmethod
	def _current_state_payload(
		automaton: Automaton, state: State, button_translations: dict[str, str] | None = None,
	) -> StatePayload:
		payload = automaton.get_state_payload(state)
		if button_translations:
			for action in payload["actions"]:
				action["ui_button"] = button_translations.get(action["name"], action["ui_button"])
		return payload


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

	# memory empty — this is a static, no-live-session estimate with no
	# real model-reported notes to seed it with; action_set carries the
	# automaton's own declared env keys, at their YAML-declared defaults,
	# for EnvPromptBlock.for_state below.
	env = Env(action_set={key.name: key.value for key in automaton.env_keys})
	has_to_evaluate_signals_before_ai_reply = not automaton.autotracking_on_ai_message
	channels = TrackingProcessor._order_channels(
		has_to_evaluate_signals_before_ai_reply,
		# Always included, unlike the live build_turn_channels — matches
		# today's implicit evaluate_signals=True default for this
		# no-live-session estimate.
		signals=SignalsChannel(signal_definition),
		reaction=ReactionChannel(reaction_definition) if automaton.reactions_enabled_for(state) else None,
		audio=AudioChannel() if automaton.talk_enabled else None,
		text=TextChannel(base_prompt),
		memory=MemoryChannel(env),
	)
	# Worst-case assumption for this state's translatable-buttons size
	# contribution: auto_tracking_enabled=False, the branch that counts
	# every action with a ui_button rather than just the untriggered
	# ones — matches what a test/manual session already shows regardless
	# of trigger (see automaton.manual_actions_for).
	originals = TrackingProcessor._button_labels_to_translate(state, auto_tracking_enabled=False)
	if originals:
		channels.append(TranslateChannel(originals))

	system_prompt = TurnProtocolUsingSchema(ai_service).build_final_prompt(channels)
	env_block = EnvPromptBlock.for_state(env, automaton, state)
	if env_block is not None:
		system_prompt = f"{system_prompt}\n\n{env_block.text()}"

	history_parts = [content_to_text(message["content"]) for message in build_priming_messages(turn_attachments)]
	history_parts.append("...")
	return "\n\n".join([system_prompt, *history_parts])
