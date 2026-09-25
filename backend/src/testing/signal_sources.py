"""Interchangeable sources of (signal_values, memory) per turn:
TurnByTurnSignalSource asks the AI once per message (high fidelity);
BatchSignalSource batches per session for fewer calls, less context."""
from __future__ import annotations

from typing import Any

from turn.sessions.env_for_session import env_for_session
from db import Db
from automaton.automaton import Automaton
from system import bus
from system.bus import POINT_CORE_SERVICES
from tracking.definitions import Signals
from tracking.env import Env
from tracking.fixed_project_context import FixedProjectContext
from tracking.tracking_service import TrackingService
from testing.replay_messages import next_assistant_message_id
from turn.turn_transaction import TurnTransaction


def _kit():
    return bus.collect(POINT_CORE_SERVICES, {})["ai_turn_kit"]


class TurnByTurnSignalSource:
    """One live AI call per turn, evaluated under the current state's
    contextual_prompt — same fidelity as production's auto-tracking, just replayed."""

    def __init__(
        self, ai_service: Any, tracking_service: TrackingService, db: Db, automaton: Automaton, session_id: int,
        env: Env, messages: list[dict],
    ) -> None:
        self._ai_service = ai_service
        self._tracking_service = tracking_service
        self._db = db
        self._automaton = automaton
        self._session_id = session_id
        self._env = env
        self._messages = messages
        self.calls_made = 0

    async def get_turn_data(self, message_id: int, current_state: str) -> tuple[dict, dict, dict]:
        kit = _kit()
        signal_names = set(self._automaton.tracked_signal_names(current_state))

        expected_row = self._db.get_signal_row_by_message(message_id)
        if expected_row is not None and expected_row['expected_state']:
            signal_names |= self._automaton.tracked_signal_names(expected_row['expected_state'])

        real_state = self._db.nearest_tracked_state_by_message(self._session_id, message_id)
        if real_state:
            signal_names |= self._automaton.tracked_signal_names(real_state)

        signal_definition = Signals(FixedProjectContext(self._automaton), self._db).get_definition(signal_names)

        state = self._automaton.get_state(current_state)
        base_prompt = f"{self._automaton.general_prompt}\n\n{state.contextual_prompt}"
        if signal_definition:
            base_prompt = f"{base_prompt}\n\n{signal_definition}"
        env_block = kit.EnvPromptBlock.for_state(self._env, self._automaton, state)
        if env_block is not None:
            base_prompt = f"{base_prompt}\n\n{env_block.text()}"
        output_definition = kit.build_output_definition_for_names(self._automaton, state.output, state.input)
        if output_definition:
            base_prompt = f"{base_prompt}\n\n{output_definition}"

        protocol = kit.TurnProtocolUsingSchema(self._ai_service)

        chat_history = self._build_chat_history(message_id)
        output_prompt = (
            kit.OutputPrompt(None, kit.build_output_fields(self._automaton, state.output)) if state.output else None
        )
        prompt = kit.Prompt.chain(
            output_prompt, kit.SignalsPrompt(None, signal_names), kit.MemoryPrompt(Env()), kit.TextPrompt(base_prompt),
        )
        signal_values: dict = {}
        stored_memory: dict = {}
        output_values: dict = {}

        def on_metadata(tag: str, value) -> None:
            if tag == 'signals':
                signal_values.update(value)
            elif tag == 'memory':
                stored_memory.update(value)
            elif tag == 'output':
                output_values.update(value)

        async for _ in protocol.generate_reply(prompt, chat_history, on_metadata):
            pass
        self.calls_made += 1

        return signal_values, stored_memory, output_values

    def _build_chat_history(self, message_id: int) -> list[dict]:
        return [{"role": m["role"], "content": m["content"]} for m in self._messages if m["id"] <= message_id]
TOKENS_PER_SIGNAL_VALUE_ESTIMATE = 2
TOKENS_PER_ENV_TURN_ESTIMATE = 40
BATCH_OUTPUT_BUDGET_SAFETY_MARGIN = 0.7


def estimate_max_turns_per_call(signal_count: int, max_output_tokens: int) -> int:
    """Shared by TestReplayJob._chunk_into_batches (deciding each batch's
    real turn grouping upfront) and TestingService._count_batch_segments
    (the matching upfront step-count estimate) — both use the project's
    full signal count as the worst case, before any state has been
    visited, so the declared step count and the real chunking agree."""
    per_turn_tokens = signal_count * TOKENS_PER_SIGNAL_VALUE_ESTIMATE + TOKENS_PER_ENV_TURN_ESTIMATE
    budget = max_output_tokens * BATCH_OUTPUT_BUDGET_SAFETY_MARGIN
    return max(1, int(budget // per_turn_tokens))

TURN_HORIZON_INSTRUCTIONS = (
    " Rate each turn only on the messages that come before the next turn's "
    "'[Turn N+1]' label — for the last turn, on everything shown — as if the "
    "later messages did not exist yet: they are there only so that one call can "
    "cover several turns. Never let what happens later change the value you give "
    "to an earlier turn."
)

BATCH_TAG_INSTRUCTIONS = (
    "Below is a conversation transcript to analyze, not a conversation to "
    "reply to — do not write a reply to it, only fill in the 'signals' and "
    "'memory' fields, following their own format definitions (a numbered row/entry "
    "per turn, one field format each). Each user turn you are being asked to "
    "cover is marked with its own '[Turn N]' label in the transcript, numbered "
    "1, 2, 3, ... with no gaps — use that exact number when numbering the "
    "corresponding row/entry in 'signals' and 'memory'; read it off the label, "
    "don't count turns or infer it yourself. The starting memory given below is "
    "read-only context from before this stretch of the conversation — the "
    "'memory' field's own numbered entries are what you must produce as output "
    "for each turn, not a repeat of the starting one."
) + TURN_HORIZON_INSTRUCTIONS


class BatchSignalSource(object):
    """Executes one AI call per group of turns it's handed — grouping
    (how many turns share a call) is TestReplayJob's decision, made
    upfront via prepare_batch(), not this class's. Always requests every
    signal the project declares, not just whichever state's own triggers
    need — signals are re-evaluated fresh every turn regardless of
    whether they end up driving a transition, so this is never wasted,
    and it means a single prepare_batch() call is always enough: there's
    no "discovered a new signal partway through" gap to re-cover, unlike
    an earlier design that grew its signal set turn by turn."""

    def __init__(
        self, ai_service: Any, tracking_service: TrackingService, db: Db, automaton: Automaton, session_id: int,
        env: Env, messages: list[dict],
    ) -> None:
        self._ai_service = ai_service
        self._tracking_service = tracking_service
        self._db = db
        self._automaton = automaton
        self._session_id = session_id
        self._env = env
        self._messages = messages
        self.calls_made = 0
        self._covered: dict[int, tuple[dict, dict, dict]] = {}

    async def get_turn_data(self, message_id: int, current_state: str) -> tuple[dict, dict, dict]:
        return self._covered.get(message_id, ({}, {}, {}))

    async def prepare_batch(self, turn_ids: list[int]) -> None:
        """Makes exactly one AI call covering all of `turn_ids` — called
        by TestReplayJob before it starts reading get_turn_data() for any
        of them. A no-op if they're already covered (e.g. TestReplayJob
        replaying from cache after a dependency job already ran this
        segment)."""
        if all(mid in self._covered for mid in turn_ids):
            return

        kit = _kit()
        signal_names = {s.name for s in self._automaton.signals}
        signal_definition = Signals(FixedProjectContext(self._automaton), self._db).get_definition(signal_names)

        seed_memory = self._seed_env(turn_ids[0])
        base_prompt = f"{self._automaton.general_prompt}\n\n{self._tag_instructions()}"
        base_prompt = f"{base_prompt}\n\nStarting memory (read-only context):\n{Env(memory=seed_memory).memory_as_text()}"
        if signal_definition:
            base_prompt = f"{base_prompt}\n\n{signal_definition}"
        env_block = kit.EnvPromptBlock.for_states(self._env, self._automaton, self._automaton.states.values())
        if env_block is not None:
            base_prompt = f"{base_prompt}\n\n{env_block.text()}"
        output_names = {name for state in self._automaton.states.values() for name in state.output}
        input_names = {name for state in self._automaton.states.values() for name in state.input}
        output_definition = kit.build_output_definition_for_names(self._automaton, output_names, input_names)
        if output_definition:
            base_prompt = f"{base_prompt}\n\n{output_definition}"
        base_prompt = f"{base_prompt}\n\nConversation transcript:\n{self._build_conversation_text(turn_ids)}"

        protocol = kit.TurnProtocolUsingSchema(self._ai_service)
        chat_history = [{"role": "user", "content": "Produce the structured output described above now."}]
        output_prompt = kit.OutputBatchPrompt(expected_turns=len(turn_ids)) if output_names else None
        prompt = kit.Prompt.chain(
            output_prompt,
            kit.SignalsBatchPrompt(None, expected_turns=len(turn_ids), signal_names=signal_names),
            kit.MemoryBatchPrompt(expected_turns=len(turn_ids)),
            kit.TextPrompt(base_prompt),
        )
        signals_by_turn: list[dict] = []
        memory_by_turn: list[dict] = []
        output_by_turn: list[dict] = []

        def on_metadata(tag: str, value) -> None:
            nonlocal signals_by_turn, memory_by_turn, output_by_turn
            if tag == 'signals':
                signals_by_turn = value
            elif tag == 'memory':
                memory_by_turn = value
            elif tag == 'output':
                output_by_turn = value

        async for _ in protocol.generate_reply(prompt, chat_history, on_metadata):
            pass
        self.calls_made += 1

        for i, turn_id in enumerate(turn_ids):
            signals = signals_by_turn[i] if i < len(signals_by_turn) else {}
            memory = memory_by_turn[i] if i < len(memory_by_turn) else {}
            output = output_by_turn[i] if i < len(output_by_turn) else {}
            self._covered[turn_id] = (signals, memory, output)

    def _seed_env(self, message_id: int) -> dict:
        all_user_message_ids = self._user_message_ids()
        index = all_user_message_ids.index(message_id)
        previous_id = all_user_message_ids[index - 1] if index > 0 else None
        if previous_id is not None and previous_id in self._covered:
            return self._covered[previous_id][1]

        session = self._db.get_chat_session(self._session_id)
        if session is None or session['datetime_start'] is None:
            return {}
        return env_for_session(TurnTransaction(self._db, session["id"], []), session).memory(until=session['datetime_start'])

    def _user_message_ids(self) -> list[int]:
        return [m['id'] for m in self._messages if m['role'] == 'user']

    def _tag_instructions(self) -> str:
        return BATCH_TAG_INSTRUCTIONS

    def _shown_content(self, message: dict) -> str:
        return message['content']

    def _cutoff_id(self, last_turn_id: int) -> int:
        if not self._automaton.autotracking_on_ai_message:
            return last_turn_id
        by_id = {m['id']: m for m in self._messages}
        return next_assistant_message_id(sorted(by_id), by_id, last_turn_id) or last_turn_id

    def _build_conversation_text(self, turn_ids: list[int]) -> str:
        """Session history up to and including the last turn this call
        covers, flattened into plain text — not passed as the provider's
        own native multi-turn message array. A real multi-turn history
        primes a chat model to reply to the latest message; this call
        needs the opposite framing (analyze N independent turns as data,
        produce no reply at all), so the whole transcript is embedded
        directly in the prompt as a document to read, with the actual API
        call carrying only a one-line trigger message (see _call_from).
        Each turn actually being numbered in this call's 'signals'/'memory'
        output gets an explicit "[Turn N]" label right before its user
        message. Without this, the model has to infer its own local 1-based
        numbering from a (possibly much longer) history that already
        carries its own absolute position, and reliably gets the two
        confused; with it, the model reads the number straight off the
        transcript instead of counting."""
        turn_number_by_message_id = {
            user_message_id: turn_number for turn_number, user_message_id in enumerate(turn_ids, start=1)
        }
        cutoff_id = self._cutoff_id(turn_ids[-1])
        lines = []
        for m in self._messages:
            if m["id"] > cutoff_id:
                continue
            turn_number = turn_number_by_message_id.get(m["id"])
            if turn_number is not None:
                lines.append(f"[Turn {turn_number}]")
            role_label = "User" if m["role"] == "user" else "Assistant"
            lines.append(f"{role_label}: {self._shown_content(m)}")
        return "\n".join(lines)


ASSISTANT_EXCERPT_CHARS = 100

BATCH_LITE_TAG_INSTRUCTIONS = (
    "Below is a conversation transcript to analyze, not a conversation to "
    "reply to — do not write a reply to it, only fill in the 'signals' and "
    "'memory' fields, following their own format definitions (a numbered row/entry "
    "per turn, one field format each). Both sides of the conversation are shown. "
    "The user's messages are shown in full; to save space, each long assistant "
    "message is shortened to its beginning and its end, with '…' where the "
    "middle was cut. Judge each turn from what is shown. Each user turn you are "
    "being asked to cover is marked with its own '[Turn N]' label in the "
    "transcript, numbered 1, 2, 3, ... with no gaps — use that exact number when "
    "numbering the corresponding row/entry in 'signals' and 'memory'; read it off "
    "the label, don't count turns or infer it yourself. The starting memory given "
    "below is read-only context from before this stretch of the conversation — the "
    "'memory' field's own numbered entries are what you must produce as output for "
    "each turn, not a repeat of the starting one."
) + TURN_HORIZON_INSTRUCTIONS


class BatchLiteSignalSource(BatchSignalSource):

    def _tag_instructions(self) -> str:
        return BATCH_LITE_TAG_INSTRUCTIONS

    def _shown_content(self, message: dict) -> str:
        content = message['content']
        if message['role'] != 'assistant' or len(content) <= 2 * ASSISTANT_EXCERPT_CHARS + 1:
            return content
        return f"{content[:ASSISTANT_EXCERPT_CHARS]}…{content[-ASSISTANT_EXCERPT_CHARS:]}"
