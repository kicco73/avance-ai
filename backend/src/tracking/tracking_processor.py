
from dataclasses import dataclass, field, replace
from datetime import datetime
from http import HTTPStatus
from typing import TYPE_CHECKING, Any, AsyncIterator

from turn.errors import TurnServiceError
from db.db import Db
from ai import AiService
from ai import MetadataCallback, content_to_text
from automaton.automaton import Action, Automaton, State, StatePayload
from automaton.choice import ChoiceSelection
from system import bus
from system.bus import POINT_SPOKEN_REPLY
from system.logging_factory import LoggerFactory
from system.web_session import WebSession
from automaton.project_services import ProjectServices
from tracking.spoken_reply import SpokenReply
from talker import AiTalker

if TYPE_CHECKING:
	from talker import BaseTalker
	from .project_files import ProjectFiles

from .env import Env
from .env_prompt_block import EnvPromptBlock
from .evaluation_scope import EvaluationScopeBuilder
from .prompt import (
	AudioPrompt, MemoryPrompt, OutputPrompt, Prompt, ReactionPrompt, SignalsPrompt, TextPrompt, TranslatePrompt,
	build_output_definition_for_names,
)
from .attachments import load_attachments
from .priming import build_priming_messages
from .project_files import project_files_for
from .sources import SourceNamespace, ToolSet
from .tracking_engine import DbTrackingSink, TrackingEngine
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


def _turn_attachment_paths(automaton: Automaton, state: State, include_signal_attachments: bool) -> list[str]:
	"""Resolved attachment paths a turn in `state` sends, in order:
	global, then `state`'s own, then — only when `include_signal_attachments`
	(the request is actually asking the model for 'signals', see
	_evaluate_signals_for) — each triggerable signal's own, in the same
	declaration order Signals.get_definition already uses for that
	signal's definition text, so a signal's attachments always travel
	with its definition. Deduplicated by resolved path: a file declared
	both globally and on a signal is sent once. Shared by the live turn
	(TrackingProcessor.__build_turn_prompt_parts) and the static
	per-state estimate (estimate_state_prompt) — they must stay
	identical."""
	paths = [*automaton.general_attachments, *state.attachments]
	if include_signal_attachments:
		signal_names = automaton.tracked_signal_names(state.key)
		for signal in automaton.signals:
			if signal.name in signal_names:
				paths.extend(signal.attachments)
	return list(dict.fromkeys(paths))

@dataclass
class Metadata:
	on_metadata: MetadataCallback
	memory: dict[str, str]
	signals: dict[str, float]
	output: dict[str, Any] = field(default_factory=dict)
	audio: str | None = None
	chunk: str | None = None
	reaction: str | None = None
	input_tokens: int | None = None
	output_tokens: int | None = None
	cache_read_tokens: int | None = None
	tool_calls: list[dict] = field(default_factory=list)
	button_translations: dict[str, str] = field(default_factory=dict)

@dataclass(frozen=True, slots=True)
class UserVariables:
	automaton: Automaton
	state: State
	project_id: str
	session_id: int
	message_id: int | None = None
	has_ai_started_conversation: bool = False

@dataclass
class OutVariables:
	reply: str
	messages: list[dict]
	tracking_id: int | None
	state: State
	action: Action | None
	tracking_linked_to_message: bool = False
	signals_resolved: bool = False
	env_changed: dict = field(default_factory=dict)

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
			  input_token_budget_per_turn: int | None = 16000,
			  assistant_talker: "BaseTalker | None" = None,
		):
		self.ai_service = ai_service
		self.assistant_talker = assistant_talker if assistant_talker is not None else AiTalker(ai_service=ai_service)

		self.env = env
		self.db = db
		self.user = user_variables
		self.auto_tracking_enabled = auto_tracking_enabled
		self.moved_before_reply = False
		self.input_token_budget_per_turn = input_token_budget_per_turn
		self._tracking_engine = TrackingEngine(DbTrackingSink(db), env, scope_builder, auto_tracking_enabled)

	async def _get_ai_reply(self) -> OutVariables:
		raise NotImplementedError

	def _open_turn(self, fragments: list[str], user_message_ids: list[int] | None) -> None:
		"""Binds this turn to the user message that closes it — the LAST
		fragment (see TurnService's own coalescing): its Tracking row, the
		bot's reaction and the input tokens all land there. Every fragment
		is already persisted by then, in arrival order, so nothing is
		saved here.

		A turn nobody started has no user message and is bound to none.
		It used to save a `'...'` one and delete it again once the reply
		existed, which made a row that briefly existed: anyone reading the
		transcript in the meantime saw it (the editor's Run panel did, and
		showed it as something the person had said).

		Also stamps this turn's own start (see self._turn_started_at's own
		use in process()), before anything else this turn writes, so it
		can never postdate a tool write this same turn later makes."""
		self._turn_started_at = datetime.utcnow()
		self._fragment_ids = list(user_message_ids or [])
		self.user = replace(
			self.user,
			message_id=self._fragment_ids[-1] if self._fragment_ids else None,
			has_ai_started_conversation=not fragments,
		)

	async def process(
		self,
		text: str | list[str] | None,
		on_metadata: MetadataCallback | None = None,
		user_message_ids: list[int] | None = None,
	) -> dict:
		"""`text` is the batch of user messages this one reply answers
		together (see PROJECT_SPECS.md §0.1). Empty for an AI-initiated
		reply. Signals and triggers are evaluated once, for the whole
		batch."""
		fragments = [text] if isinstance(text, str) else list(text or [])
		state = self.user.state

		if not state.chat_enabled and [f for f in fragments if f not in ("", "...")]:
			raise TrackingServiceError(
				"This state doesn't accept messages; use an action instead.", status_code=HTTPStatus.CONFLICT,
				code="state_not_chat",
			)

		self._open_turn(fragments, user_message_ids)

		self.metadata = Metadata(on_metadata or (lambda key, value: None), {}, {})
		self.metadata.on_metadata("typing", None)
		self.out = await self._get_ai_reply()

		logger.info(
			"process() got metadata.audio=%r for session %s", self.metadata.audio, self.user.session_id,
		)
		assistant_id = self.db.save_message(
			"assistant", self.out.reply, self.user.session_id,
			audio_text=self.metadata.audio, tokens=self.metadata.output_tokens,
		)
		landed_on_a_cleared_state_with_no_reply_after_the_clear = (
			self.out.action is not None and self.out.state.ai_memory_strategy == "clear" and not self.moved_before_reply
		)
		if not landed_on_a_cleared_state_with_no_reply_after_the_clear:
			self.env.update(self.metadata.memory, message_id=assistant_id, declared_keys=self.user.automaton.declared_env_key_names())
		self.db.mark_messages_answered(self._fragment_ids, assistant_id)

		if self.metadata.tool_calls:
			self.db.record_tool_calls(self.user.session_id, self.metadata.tool_calls, message_id=assistant_id)
		self.db.link_tool_env_writes_to_message(self.user.session_id, assistant_id, since=self._turn_started_at)

		if self.out.tracking_id is not None and not self.out.tracking_linked_to_message:
			self.db.link_signal_to_message(self.out.tracking_id, assistant_id)

		user_message_id = self.user.message_id
		if self.metadata.reaction and user_message_id is not None:
			self.db.set_message_reaction(user_message_id, self.metadata.reaction)

		if self.metadata.input_tokens is not None and user_message_id is not None:
			self.db.set_message_tokens(user_message_id, self.metadata.input_tokens, self.metadata.cache_read_tokens or 0)

		return self._build_turn_response(user_message_id, assistant_id)

	def _apply_output_to_env(self, state: State) -> None:
		output_for_env = {
			name: value for name, value in self.metadata.output.items() if name in state.output
		}
		if output_for_env:
			self.env.update_action_set(output_for_env, origin="output")
			self.out.env_changed.update(output_for_env)

	def on_receiving_metadata(self, key: str, value: Any) -> None:
		"""Shared by every generate call this processor (or a subclass)
		makes — values arrive here already decoded through their own
		channel of the composed Prompt (see TurnProtocolUsingSchema.
		generate_reply), so this only ever stores them or drives a real
		side effect, never parses raw model output itself. 'signals' is
		the one branch with a genuine side effect (a fresh signals value
		may trigger an automaton transition) — safe to fire
		unconditionally even on a call whose own composed prompt never
		includes SignalsPrompt (the regeneration call in
		TrackingProcessorAfterUserMessage): a schema-constrained provider
		structurally can't emit a field outside the schema it was given,
		so this branch is simply unreachable there, same reasoning
		already relied on for 'reaction'/'memory'/'audio' being a strict
		superset of what any one call actually requests. 'output' arrives
		before 'signals' (see Prompt.chain ordering) and is stored for
		trigger/env evaluation in _resolve_signals."""
		rv = value
		if key == 'output':
			rv = self.metadata.output = value or {}
		elif key == 'signals':
			self._resolve_signals(value)
		elif key == 'memory':
			self.metadata.memory = value
		elif key == 'audio':
			self.metadata.audio = value
		elif key == 'reaction':
			self.metadata.reaction = value
		elif key == 'translations':
			self.metadata.button_translations = value
		elif key == 'input_tokens':
			rv = self.metadata.input_tokens = (self.metadata.input_tokens or 0) + value
		elif key == 'output_tokens':
			self.metadata.output_tokens = value
		elif key == 'cache_read_tokens':
			rv = self.metadata.cache_read_tokens = (self.metadata.cache_read_tokens or 0) + value
		elif key == 'tool' and value.get('phase') == 'result':
			self.metadata.tool_calls.append({
				"name": value["name"], "arguments": value["arguments"], "result": value["result"],
				"label": value["label"], "rows": value["rows"], "error": value["error"],
				"duration_ms": value["duration_ms"],
			})
		self.metadata.on_metadata(key, rv)

	def _resolve_signals(self, signal_values: dict[str, float]) -> None:
		"""The one trigger-evaluation pass of a turn: `signal_values` are
		the model's own reported signals when they were requested, or the
		empty set when they weren't (see _evaluate_signals_for) — a state
		whose triggers reference only metric.*/env.*/tool.* is evaluated
		every chat turn all the same, exactly as one with signal-backed
		triggers; a signal-backed trigger evaluated against the empty set
		simply short-circuits to false (see Automaton._eval_trigger). At this
		point (when signals arrive in streaming), self.metadata.output is
		already populated from the earlier 'output' field arrival (see
		on_receiving_metadata's ordering)."""
		self.metadata.signals = signal_values
		self.out.action = self._tracking_engine.evaluate_triggered_action(
			self.user.automaton, self.user.state, self.metadata.signals, ChoiceSelection.NONE,
			session_id=self.user.session_id, output_values=self.metadata.output,
		)
		if self.out.action:
			self.out.state = self.user.automaton.get_state(self.out.action.target)
		self.out.signals_resolved = True

	def _records_evaluation(self) -> bool:
		"""Whether this turn's trigger evaluation leaves a Tracking row: a
		fired transition always does; an evaluation with no transition
		only when the model actually reported signals worth a snapshot
		(see TrackingEngine.apply_transition) — one against the empty set
		has nothing new to record. A state with its own `output` also earns a
		row on its own even with no signals/trigger at all — otherwise a
		state that only ever produces output, never fires an action off
		it, would never get its output linked to a message (see
		TurnService.get_output's own message_id lookup)."""
		return bool(self.metadata.signals) or bool(self.metadata.output) or self.out.action is not None

	def generate_reply(self, state: State, on_metadata: MetadataCallback) -> AsyncIterator[str]:
		base_prompt, output_definition, signal_definition, reaction_definition, turn_attachments = self.__build_turn_prompt_parts(
			self.user.automaton, state, self._evaluate_signals_for(state),
		)
		prompt = self.build_turn_prompt(state, base_prompt, output_definition, signal_definition, reaction_definition)
		env_block = EnvPromptBlock.for_state(self.env, self.user.automaton, state)
		remaining_history_budget = self._enforce_input_budget(
			base_prompt, output_definition, signal_definition, reaction_definition, turn_attachments, prompt, env_block,
		)
		chat_history = self._build_chat_history(turn_attachments, remaining_history_budget)

		return self.assistant_talker.chat(
			prompt, chat_history, on_metadata,
			tool_set=self.build_tool_set(state), force_required_tools=self.force_required_tools_for(state),
			env_block=env_block.text() if env_block else None,
		)

	def _enforce_input_budget(
		self, base_prompt: str, output_definition: str | None, signal_definition: str | None, reaction_definition: str | None,
		turn_attachments: list, prompt: Prompt | None = None,
		env_block: "EnvPromptBlock | None" = None,
	) -> int | None:
		budget = self.input_token_budget_per_turn
		if budget is None:
			return None
		schema_overhead = prompt.schema_overhead_text() if prompt is not None else ""
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
		self.db.save_system_warning(WebSession().user, self.user.project_id, "input_budget_exceeded", message)
		raise TurnServiceError(
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
		given here too, so a write tool (a source's `update`) can
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

	def _build_base_prompt_and_history(self, state: State) -> tuple[Prompt, list[dict], "EnvPromptBlock | None"]:
		"""The same (prompt, chat_history, env_block) the transition-
		regeneration path (TrackingProcessorAfterUserMessage) actually
		sends for `state` — exposed single-underscore (rather than
		name-mangled) so the caller sends this call's own prompt, not a
		second one rebuilt from its pieces.
		`env_block` is handed back rather than recomputed by the caller —
		EnvPromptBlock.for_state reads through self.env/automaton, no
		reason to do that twice for one regeneration call.
		Signal attachments are never included here: build_regeneration_prompt
		never composes SignalsPrompt (signals are already known from the
		first call), so nothing in this call's own request references
		them."""
		base_prompt, output_definition, signal_definition, reaction_definition, turn_attachments = self.__build_turn_prompt_parts(
			self.user.automaton, state, False,
		)
		prompt = self.build_regeneration_prompt(state, base_prompt, output_definition)
		env_block = EnvPromptBlock.for_state(self.env, self.user.automaton, state)
		remaining_history_budget = self._enforce_input_budget(
			base_prompt, output_definition, signal_definition, reaction_definition, turn_attachments, prompt, env_block,
		)
		return prompt, self._build_chat_history(turn_attachments, remaining_history_budget), env_block

	def _build_chat_history(self, turn_attachments: list, token_budget: int | None) -> list[dict]:
		priming_messages = build_priming_messages(turn_attachments)
		since = self.db.history_cutoff_for_session(self.user.session_id, self.user.state.history_cutoff)
		history = priming_messages + self._strip_timestamps(
			self.db.get_turn_history(self.user.session_id, since, token_budget)
		)
		return history + [{"role": "user", "content": "..."}] * self.user.has_ai_started_conversation

	def build_turn_prompt(
		self, state: State, base_prompt: str, output_definition: str | None, signal_definition: str | None, reaction_definition: str | None,
	) -> Prompt:
		"""The full, gated Prompt for a reply generated in `state` —
		whichever state this turn's own reply is actually about to be
		generated in. Every caller except the regenerate-after-transition
		path (TrackingProcessorAfterUserMessage) passes self.user.state
		(the turn's own pre-transition state); that one path must pass
		its own self.out.state instead: the gating below is about what's
		triggerable/translatable from THAT state, not the one the turn
		started in. OutputPrompt is always first, before signals (so output
		values are available to triggers when signals arrive)."""
		has_to_evaluate_signals_before_ai_reply = not self.user.automaton.autotracking_on_ai_message
		talk_enabled = _spoken_reply_wanted(self.user.automaton.services, self.user.session_id)
		logger.info(
			"build_turn_prompt spoken reply: project=%r revision=%s session=%s -> %s",
			self.user.project_id, self.user.automaton.revision, self.user.session_id, talk_enabled,
		)
		reactions_enabled = self.user.automaton.reactions_enabled_for(self.user.state)

		output = OutputPrompt(output_definition) if state.output else None
		signals = SignalsPrompt(signal_definition) if self._evaluate_signals_for(state) else None
		reaction = ReactionPrompt(reaction_definition) if reactions_enabled else None
		audio = AudioPrompt() if talk_enabled else None
		text = TextPrompt(base_prompt)
		memory = MemoryPrompt(self.env)
		if has_to_evaluate_signals_before_ai_reply:
			prompt = Prompt.chain(output, signals, reaction, audio, text, memory)
		else:
			prompt = Prompt.chain(audio, text, output, signals, reaction, memory)
		return self._append_translate_prompt(prompt, state)

	def _evaluate_signals_for(self, state: State) -> bool:
		"""Whether a reply generated in `state` should even ask the model
		for 'signals' — see build_turn_prompt' own docstring on the
		"before"/"after" strategies; pointless when nothing in `state`
		could trigger from them (no definition in the prompt, no
		'signals' field in the schema). This gates the *request* only,
		never the trigger evaluation itself: a turn with a real user
		message that asks for nothing still runs _resolve_signals against
		the empty set, so a trigger referencing only metric.*/env.*/
		tool.* keeps firing — except at the opening turn (see
		TrackingProcessorAfterUserMessage._get_ai_reply's own has_ai_
		started_conversation branch), which skips that evaluation outright
		rather than let the automaton's own AI-generated opener alone fire
		a transition nothing in the conversation asked for. Exposed on
		its own (not just inlined in build_turn_prompt) so a caller can
		know this upfront without building the full prompt — see
		TrackingProcessorAfterUserMessage's own upfront resolution."""
		has_to_evaluate_signals_before_ai_reply = not self.user.automaton.autotracking_on_ai_message
		return (
			not (has_to_evaluate_signals_before_ai_reply and self.user.has_ai_started_conversation)
		) and bool(self.user.automaton.tracked_signal_names(state.key))

	def build_regeneration_prompt(self, state: State, base_prompt: str, output_definition: str | None = None) -> Prompt:
		"""The (audio, text, output, memory) Prompt the transition-
		regeneration call sends — signals are already known from the first
		call and must not be re-requested; talk_enabled/reaction gating
		never applied here either (pre-existing behavior, preserved as-is).
		Output is requested when `state` (the real post-transition state)
		declares any: the first call only ever asked for the state the turn
		started in, so a state reached mid-turn, only through a trigger, has
		never had its own output fields offered to the model before this
		call. `state` is also the one call site that actually knows the
		post-transition state at prompt-build time — so this is where
		button translation is genuinely correct after a transition."""
		output = OutputPrompt(output_definition) if state.output else None
		prompt = Prompt.chain(AudioPrompt(), TextPrompt(base_prompt), output, MemoryPrompt(self.env))
		return self._append_translate_prompt(prompt, state)

	def _append_translate_prompt(self, prompt: Prompt, state: State) -> Prompt:
		originals = self._button_labels_to_translate(state, self.auto_tracking_enabled)
		if originals:
			return prompt.compose(TranslatePrompt(originals))
		return prompt

	@staticmethod
	def _button_labels_to_translate(state: State, auto_tracking_enabled: bool) -> dict[str, str]:
		"""{action name: original ui_button text} for every action `state`
		would show as a manual button — same filter as
		automaton.pressable_actions, over live Action objects instead of
		serialized ActionPayload dicts, plus requiring a non-empty
		ui_button (nothing to translate otherwise)."""
		return {
			a.name: a.ui_button for a in state.actions
			if (a.trigger is None or not auto_tracking_enabled) and a.ui_button
		}

	def __build_turn_prompt_parts(
		self, automaton: Automaton, state: State, include_signal_attachments: bool,
	) -> tuple[str, str | None, str | None, str | None, list]:

		if state.fixed_message:
			logger.warning("Translating fixed_message for state '%s'.", state.key)
			return FIXED_MESSAGE_INSTRUCTIONS.format(fixed_message=state.fixed_message), None, None, None, []
		output_definition = build_output_definition_for_names(automaton, state.output, state.input)
		signals = Signals(FixedProjectContext(automaton), self.db)
		signal_names = automaton.tracked_signal_names(state.key)
		signal_definition = signals.get_definition(signal_names)
		reaction_definition = self._build_reaction_definition(automaton) if automaton.reactions_enabled_for(state) else None
		base_prompt = f"{automaton.general_prompt}\n\n{state.contextual_prompt}"
		return (
			base_prompt, output_definition, signal_definition, reaction_definition,
			load_attachments(
				project_files_for(self.db, automaton),
				_turn_attachment_paths(automaton, state, include_signal_attachments),
			),
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
		reply = [self.db.get_message(assistant_message_id)] if assistant_message_id is not None else []
		return {
			"reply": reply,
			"user_message_id": user_message_id,
			"assistant_message_id": assistant_message_id,
			"user_message_reaction": self.metadata.reaction if user_message_id is not None else None,
			"state": self._current_state_payload(self.user.automaton, self.out.state, self.metadata.button_translations),
			"state_changed": action is not None,
			"moved_before_reply": self.moved_before_reply,
			"from_state": self.user.state.key if action else None,
			"new_state": action.target if action else None,
			"env_changed": dict(self.out.env_changed),
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


def _spoken_reply_wanted(services: ProjectServices, session_id: int | None) -> bool:
	"""Asks whoever can speak, and whoever runs the interface this session
	is being had on (see bus.POINT_SPOKEN_REPLY). Nobody registered means
	nothing speaks and nothing wants it, which is the right answer for a
	build without those packages — and the reason this is a question and
	not the `talk_enabled` argument it used to be, threaded from main.py
	through TrackingService to be combined with a name core had to know."""
	return bus.collect(POINT_SPOKEN_REPLY, SpokenReply(services=services, session_id=session_id)).asked


def _spoken_reply_possible(services: ProjectServices) -> bool:
	"""The same question with no session to ask about: a static estimate
	has no interface and no toggle, so it stands in for one that wants a
	spoken reply and asks only whether this build could speak it."""
	spoken = SpokenReply(services=services)
	spoken.want()
	return bus.collect(POINT_SPOKEN_REPLY, spoken).asked


def estimate_state_prompt(
	automaton: Automaton, state: State, files: "ProjectFiles",
) -> str:
	"""The system_prompt TrackingProcessor.generate_reply would actually
	send for `state`, plus a synthetic one-turn history standing in for a
	real conversation — a single '...' placeholder user message, preceded
	by this state's own attachments, global attachments, and (since
	signals_prompt below is always composed) every triggerable signal's
	own attachments too — see _turn_attachment_paths, the same
	composition a real turn's own __build_turn_prompt_parts uses.
	Renders with no live session/Db needed, for ProjectInspector.
	get_state_input_tokens' own per-state input-token estimate. `files`
	comes from the caller rather than from a Db this function doesn't
	have: the estimate counts the attachment bytes a real turn would
	actually send, so it needs the same reader that turn would use
	(ProjectInspector.get_state_input_tokens has one)."""
	if state.fixed_message:
		base_prompt = FIXED_MESSAGE_INSTRUCTIONS.format(fixed_message=state.fixed_message)
		output_definition = None
		signal_definition = None
		reaction_definition = None
		turn_attachments: list = []
	else:
		output_definition = build_output_definition_for_names(automaton, state.output, state.input)
		signals = Signals(FixedProjectContext(automaton), None)
		signal_definition = signals.get_definition(automaton.tracked_signal_names(state.key))
		reaction_definition = (
			TrackingProcessor._build_reaction_definition(automaton) if automaton.reactions_enabled_for(state) else None
		)
		base_prompt = f"{automaton.general_prompt}\n\n{state.contextual_prompt}"
		turn_attachments = load_attachments(files, _turn_attachment_paths(automaton, state, True))
	env = Env(action_set={key.name: key.value for key in automaton.env_keys})
	has_to_evaluate_signals_before_ai_reply = not automaton.autotracking_on_ai_message
	output_prompt = OutputPrompt(output_definition) if state.output else None
	signals_prompt = SignalsPrompt(signal_definition)
	reaction_prompt = ReactionPrompt(reaction_definition) if automaton.reactions_enabled_for(state) else None
	audio_prompt = AudioPrompt() if _spoken_reply_possible(automaton.services) else None
	text_prompt = TextPrompt(base_prompt)
	memory_prompt = MemoryPrompt(env)
	if has_to_evaluate_signals_before_ai_reply:
		prompt = Prompt.chain(output_prompt, signals_prompt, reaction_prompt, audio_prompt, text_prompt, memory_prompt)
	else:
		prompt = Prompt.chain(audio_prompt, text_prompt, output_prompt, signals_prompt, reaction_prompt, memory_prompt)
	originals = TrackingProcessor._button_labels_to_translate(state, auto_tracking_enabled=False)
	if originals:
		prompt = prompt.compose(TranslatePrompt(originals))

	system_prompt = prompt.render_text()
	env_block = EnvPromptBlock.for_state(env, automaton, state)
	if env_block is not None:
		system_prompt = f"{system_prompt}\n\n{env_block.text()}"

	history_parts = [content_to_text(message["content"]) for message in build_priming_messages(turn_attachments)]
	history_parts.append("...")
	return "\n\n".join([system_prompt, *history_parts])
