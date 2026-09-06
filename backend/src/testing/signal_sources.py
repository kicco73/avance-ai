"""Interchangeable sources of (signal_values, memory) per turn:
TurnByTurnSignalSource asks the AI once per message (high fidelity);
BatchSignalSource batches per session for fewer calls, less context;
BatchLiteSignalSource is the same batching with an even lighter,
one-sided transcript (see its own docstring)."""
from __future__ import annotations

from chat.env_for_session import env_for_session
from db import Db
from ai import AiService
from automaton.automaton import Automaton
from tracking.definitions import Signals
from tracking.env import Env
from tracking.fixed_project_context import FixedProjectContext
from tracking.prompt import MemoryBatchPrompt, MemoryPrompt, Prompt, SignalsBatchPrompt, SignalsPrompt, TextPrompt
from tracking.tracking_service import TrackingService
from tracking.turn_protocol_using_schema import TurnProtocolUsingSchema
from testing.replay_messages import next_assistant_message_id


class TurnByTurnSignalSource:
    """One live AI call per turn, evaluated under the current state's
    contextual_prompt — same fidelity as production's auto-tracking, just replayed."""

    def __init__(
        self, ai_service: AiService, tracking_service: TrackingService, db: Db, automaton: Automaton, session_id: int,
    ) -> None:
        self._ai_service = ai_service
        # Unused here: TrackingService.get_definition hardcodes the "active
        # project" automaton, not necessarily this run's; kept only for
        # interface parity with how this class is constructed.
        self._tracking_service = tracking_service
        self._db = db
        self._automaton = automaton
        self._session_id = session_id
        # One real AI call per get_turn_data call, always — see
        # BatchSignalSource.calls_made for why TestReplayJob needs this on
        # both signal sources (one job "step" = one real AI call, not one
        # turn replayed).
        self.calls_made = 0

    async def get_turn_data(self, message_id: int, current_state: str) -> tuple[dict, dict]:
        signal_names = set(self._automaton.triggerable_signal_names(current_state))

        expected_row = self._db.get_signal_row_by_message(message_id)
        if expected_row is not None and expected_row['expected_state']:
            signal_names |= self._automaton.triggerable_signal_names(expected_row['expected_state'])

        nearest = self._db.get_nearest_tracking_row_by_message(self._session_id, message_id)
        if nearest is not None:
            real_state = nearest['new_state'] or nearest['old_state']
            if real_state:
                signal_names |= self._automaton.triggerable_signal_names(real_state)

        signal_definition = Signals(FixedProjectContext(self._automaton), self._db).get_definition(signal_names)

        state = self._automaton.get_state(current_state)
        base_prompt = f"{self._automaton.general_prompt}\n\n{state.contextual_prompt}"
        if signal_definition:
            base_prompt = f"{base_prompt}\n\n{signal_definition}"

        protocol = TurnProtocolUsingSchema(self._ai_service)

        chat_history = self._build_chat_history(message_id)

        # signal_definition is already folded into base_prompt above, so
        # SignalsPrompt carries no content of its own here — TextPrompt
        # goes last, reproducing the old generate_reply_with_schema
        # convention of appending base_prompt after every channel's own
        # definition/content.
        prompt = Prompt.chain(SignalsPrompt(None), MemoryPrompt(Env()), TextPrompt(base_prompt))
        signal_values: dict = {}
        stored_memory: dict = {}

        def on_metadata(tag: str, value) -> None:
            if tag == 'signals':
                signal_values.update(value)
            elif tag == 'memory':
                stored_memory.update(value)

        async for _ in protocol.generate_reply(prompt, chat_history, on_metadata):
            pass
        self.calls_made += 1

        return signal_values, stored_memory

    def _build_chat_history(self, message_id: int) -> list[dict]:
        messages = self._db.get_messages(self._session_id)
        return [{"role": m["role"], "content": m["content"]} for m in messages if m["id"] <= message_id]


# Rough per-turn output-token cost used to cap how many turns one batch
# call asks for at once (see TestReplayJob._chunk_into_batches, which owns
# this decision upfront — BatchSignalSource itself just executes whatever
# group it's handed) — a heuristic, not a hard guarantee, since env's
# actual length varies with what the model reports. Keeps a batch call
# from asking for more turns than can plausibly fit before the provider
# truncates the response (see AIServiceProviderOutputTruncatedError) —
# better to split into another call than to lose an unbounded tail of
# turns to truncation.
TOKENS_PER_SIGNAL_VALUE_ESTIMATE = 2
TOKENS_PER_ENV_TURN_ESTIMATE = 40
BATCH_OUTPUT_BUDGET_SAFETY_MARGIN = 0.7


def estimate_max_turns_per_call(signal_count: int, max_output_tokens: int) -> int:
    """Shared by TestReplayJob._chunk_into_batches (deciding each batch's
    real turn grouping upfront) and TestService._count_batch_segments
    (the matching upfront step-count estimate) — both use the project's
    full signal count as the worst case, before any state has been
    visited, so the declared step count and the real chunking agree."""
    per_turn_tokens = signal_count * TOKENS_PER_SIGNAL_VALUE_ESTIMATE + TOKENS_PER_ENV_TURN_ESTIMATE
    budget = max_output_tokens * BATCH_OUTPUT_BUDGET_SAFETY_MARGIN
    return max(1, int(budget // per_turn_tokens))

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
)


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
        self, ai_service: AiService, tracking_service: TrackingService, db: Db, automaton: Automaton, session_id: int,
    ) -> None:
        self._ai_service = ai_service
        self._tracking_service = tracking_service
        self._db = db
        self._automaton = automaton
        self._session_id = session_id
        self.calls_made = 0
        # message_id -> (signal_values, memory) for every turn a
        # prepare_batch() call has covered so far.
        self._covered: dict[int, tuple[dict, dict]] = {}

    async def get_turn_data(self, message_id: int, current_state: str) -> tuple[dict, dict]:
        # current_state unused: prepare_batch() already covered every
        # turn in its group for every project signal, regardless of
        # which state a given turn lands in — see the class docstring.
        return self._covered.get(message_id, ({}, {}))

    async def prepare_batch(self, turn_ids: list[int]) -> None:
        """Makes exactly one AI call covering all of `turn_ids` — called
        by TestReplayJob before it starts reading get_turn_data() for any
        of them. A no-op if they're already covered (e.g. TestReplayJob
        replaying from cache after a dependency job already ran this
        segment)."""
        if all(mid in self._covered for mid in turn_ids):
            return

        signal_names = {s.name for s in self._automaton.signals}
        signal_definition = Signals(FixedProjectContext(self._automaton), self._db).get_definition(signal_names)

        seed_memory = self._seed_env(turn_ids[0])
        base_prompt = f"{self._automaton.general_prompt}\n\n{self._tag_instructions()}"
        base_prompt = f"{base_prompt}\n\nStarting memory (read-only context):\n{Env(memory=seed_memory).memory_as_text()}"
        if signal_definition:
            base_prompt = f"{base_prompt}\n\n{signal_definition}"
        base_prompt = f"{base_prompt}\n\nConversation transcript:\n{self._build_conversation_text(turn_ids)}"

        protocol = TurnProtocolUsingSchema(self._ai_service)

        # Not the real conversation as native multi-turn messages — see
        # _build_conversation_text, which already flattened it into
        # base_prompt above as a document to analyze. A chat API still
        # needs at least one message to generate against; this one line
        # is that trigger, not part of the data being analyzed.
        chat_history = [{"role": "user", "content": "Produce the structured output described above now."}]

        # signal_definition/seed_memory are already folded into base_prompt
        # above, so both channels carry no content of their own here —
        # TextPrompt goes last, same convention as TurnByTurnSignalSource's.
        # Index i = turn i+1 (see SignalsBatchPrompt/MemoryBatchPrompt's
        # own decode) — empty until on_metadata actually fires for that
        # tag, which never happens if the response was truncated before
        # reaching it (see AIServiceProviderOutputTruncatedError) — every
        # turn then falls back to {} below, same as a turn a mismatch
        # check would have rejected.
        prompt = Prompt.chain(
            SignalsBatchPrompt(None, expected_turns=len(turn_ids)),
            MemoryBatchPrompt(expected_turns=len(turn_ids)),
            TextPrompt(base_prompt),
        )
        signals_by_turn: list[dict] = []
        memory_by_turn: list[dict] = []

        def on_metadata(tag: str, value) -> None:
            nonlocal signals_by_turn, memory_by_turn
            if tag == 'signals':
                signals_by_turn = value
            elif tag == 'memory':
                memory_by_turn = value

        async for _ in protocol.generate_reply(prompt, chat_history, on_metadata):
            pass
        self.calls_made += 1

        for i, turn_id in enumerate(turn_ids):
            signals = signals_by_turn[i] if i < len(signals_by_turn) else {}
            memory = memory_by_turn[i] if i < len(memory_by_turn) else {}
            self._covered[turn_id] = (signals, memory)

    def _seed_env(self, message_id: int) -> dict:
        all_user_message_ids = self._user_message_ids()
        index = all_user_message_ids.index(message_id)
        previous_id = all_user_message_ids[index - 1] if index > 0 else None
        if previous_id is not None and previous_id in self._covered:
            return self._covered[previous_id][1]

        session = self._db.get_chat_session(self._session_id)
        if session is None or session['datetime_start'] is None:
            return {}
        return env_for_session(self._db, session).memory(until=session['datetime_start'])

    def _user_message_ids(self) -> list[int]:
        return [m['id'] for m in self._db.get_messages(self._session_id) if m['role'] == 'user']

    def _tag_instructions(self) -> str:
        """Framing instructions prepended before the transcript — a hook
        so a subclass whose transcript departs from "both sides, in full"
        (see _transcript_role/_anchor_message_id below) can describe that
        shape accurately instead of inheriting a description that no
        longer matches what the model is actually shown."""
        return BATCH_TAG_INSTRUCTIONS

    def _transcript_role(self) -> str | None:
        """Which message role _build_conversation_text keeps — None (the
        default) keeps both, the full back-and-forth."""
        return None

    def _anchor_message_id(self, user_message_id: int, ordered_ids: list[int], by_id: dict) -> int | None:
        """Which message's id gets a turn's own "[Turn N]" label — the
        user's message itself by default, which is always present since
        the default _transcript_role keeps both roles. A subclass that
        drops the user's side entirely must instead point this at whatever
        message of its own kept role actually stands in for that turn."""
        return user_message_id

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
        output gets an explicit "[Turn N]" label right before whichever
        message _anchor_message_id resolves it to. Without this, the model
        has to infer its own local 1-based numbering from a (possibly much
        longer) history that already carries its own absolute position,
        and reliably gets the two confused; with it, the model reads the
        number straight off the transcript instead of counting.
        _transcript_role, when not None, drops the other role's messages
        from what's shown entirely (not just at the anchor) — the default
        keeps both, so the loop below reduces to the original per-role-
        agnostic behaviour in that case."""
        messages = self._db.get_messages(self._session_id)
        by_id = {m['id']: m for m in messages}
        ordered_ids = sorted(by_id.keys())

        turn_number_by_anchor_id: dict[int, int] = {}
        cutoff_id = turn_ids[-1]
        for turn_number, user_message_id in enumerate(turn_ids, start=1):
            anchor_id = self._anchor_message_id(user_message_id, ordered_ids, by_id)
            if anchor_id is None:
                continue
            turn_number_by_anchor_id[anchor_id] = turn_number
            cutoff_id = max(cutoff_id, anchor_id)

        role = self._transcript_role()
        lines = []
        for m in messages:
            if m["id"] > cutoff_id:
                continue
            if role is not None and m["role"] != role:
                continue
            turn_number = turn_number_by_anchor_id.get(m["id"])
            if turn_number is not None:
                lines.append(f"[Turn {turn_number}]")
            role_label = "User" if m["role"] == "user" else "Assistant"
            lines.append(f"{role_label}: {m['content']}")
        return "\n".join(lines)


BATCH_LITE_TAG_INSTRUCTIONS_TEMPLATE = (
    "Below is a conversation transcript to analyze, not a conversation to "
    "reply to — do not write a reply to it, only fill in the 'signals' and "
    "'memory' fields, following their own format definitions (a numbered row/entry "
    "per turn, one field format each). To save space, only the {shown_role}'s "
    "own messages are included below — the {other_role}'s messages have been "
    "left out entirely, not merely hidden per turn — so judge each turn from "
    "the {shown_role} content shown and the surrounding {shown_role} context "
    "alone. Each turn you are being asked to cover is marked with its own "
    "'[Turn N]' label in the transcript, numbered 1, 2, 3, ... with no gaps — "
    "use that exact number when numbering the corresponding row/entry in "
    "'signals' and 'memory'; read it off the label, don't count turns or infer it "
    "yourself. The starting memory given below is read-only context from before "
    "this stretch of the conversation — the 'memory' field's own numbered entries "
    "are what you must produce as output for each turn, not a repeat of the "
    "starting one."
)


class BatchLiteSignalSource(BatchSignalSource):
    """Same batching/grouping/protocol as BatchSignalSource — only the
    transcript handed to the AI differs. Rather than the full back-and-
    forth, it carries just one side of the conversation: whichever side
    this project's own live auto-tracking actually evaluates against (see
    Automaton.autotracking_on_ai_message) — the user's messages when it
    evaluates before the AI's reply, the assistant's when it evaluates
    after (typically because the model self-reports signals inline in
    that reply — see PROJECT_SPECS.md §4.3). Roughly halves transcript
    input tokens versus BatchSignalSource without dropping the side that
    actually drives the signals it's mimicking."""

    def _tag_instructions(self) -> str:
        shown_role = self._transcript_role()
        other_role = 'user' if shown_role == 'assistant' else 'assistant'
        return BATCH_LITE_TAG_INSTRUCTIONS_TEMPLATE.format(shown_role=shown_role, other_role=other_role)

    def _transcript_role(self) -> str:
        return 'assistant' if self._automaton.autotracking_on_ai_message else 'user'

    def _anchor_message_id(self, user_message_id: int, ordered_ids: list[int], by_id: dict) -> int | None:
        if not self._automaton.autotracking_on_ai_message:
            return user_message_id
        return next_assistant_message_id(ordered_ids, by_id, user_message_id)
