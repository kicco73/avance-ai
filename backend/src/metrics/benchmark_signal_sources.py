"""Two interchangeable sources of (signal_values, stored_env) per turn:
TurnByTurnSignalSource asks the AI once per message (high fidelity);
BatchSignalSource batches per session for fewer calls, less context."""
from __future__ import annotations

from db import Db
from ai.ai_service import AiService
from automaton.automaton import Automaton
from tracking.definitions import Signals
from tracking.env import Env, PersistedEnv
from tracking.metadata_handler import MetadataHandler
from tracking.tracking_service import TrackingService
from tracking.turn_protocol_using_schema import TurnProtocolUsingSchema
from tracking.turn_protocol_using_text_extraction import TurnProcotolUsingTextExtraction


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

        signal_definition = Signals(lambda: self._automaton, self._db).get_definition(signal_names)

        state = self._automaton.get_state(current_state)
        base_prompt = f"{self._automaton.general_prompt}\n\n{state.contextual_prompt}"
        if signal_definition:
            base_prompt = f"{base_prompt}\n\n{signal_definition}"

        protocol_cls = TurnProtocolUsingSchema if self._ai_service.is_provider_with_schema() else TurnProcotolUsingTextExtraction
        # Second positional param only affects generate_reply's own tag
        # ordering, never read by generate_reply_with_schema — the only
        # method this class calls.
        protocol = protocol_cls(self._ai_service, True)

        chat_history = self._build_chat_history(message_id)

        tag_specs = [('signals', 'signals'), ('env', 'env')]
        tag_kind_by_name = dict(tag_specs)
        signal_values: dict = {}
        stored_env: dict = {}

        def on_metadata(tag: str, value: str) -> None:
            kind = tag_kind_by_name.get(tag)
            if kind == 'signals':
                signal_values.update(MetadataHandler.parse_raw_signals(value))
            elif kind == 'env':
                stored_env.update(MetadataHandler.parse_raw_env(value))

        async for _ in protocol.generate_reply_with_schema(base_prompt, tag_specs, chat_history, on_metadata):
            pass

        return signal_values, stored_env

    def _build_chat_history(self, message_id: int) -> list[dict]:
        messages = self._db.get_messages(self._session_id)
        return [{"role": m["role"], "content": m["content"]} for m in messages if m["id"] <= message_id]


BATCH_TAG_INSTRUCTIONS = (
    "This turn covers multiple messages of the conversation at once, "
    "numbered in order. Fill in signals1...signalsN and env1...envN, one "
    "pair per turn, in the exact same order the conversation happened. "
    "The starting env given below is read-only context from before this "
    "stretch of the conversation — env1...envN are what you must produce "
    "as output for each turn, not a repeat of the starting one."
)


class BatchSignalSource:
    """One AI call per session (or remaining stretch) instead of per turn,
    re-covering from the divergence point when a state needs an unasked
    signal. Uses only general_prompt, since states aren't known upfront."""

    def __init__(
        self, ai_service: AiService, tracking_service: TrackingService, db: Db, automaton: Automaton, session_id: int,
    ) -> None:
        self._ai_service = ai_service
        self._tracking_service = tracking_service
        self._db = db
        self._automaton = automaton
        self._session_id = session_id
        self.batch_segments = 0
        # message_id -> (signal_values, stored_env) for every turn the
        # most recent call(s) actually cover.
        self._covered: dict[int, tuple[dict, dict]] = {}
        self._signal_names: set[str] = set()

    async def get_turn_data(self, message_id: int, current_state: str) -> tuple[dict, dict]:
        needed = set(self._automaton.triggerable_signal_names(current_state))

        if message_id in self._covered:
            existing_values, _ = self._covered[message_id]
            if needed <= set(existing_values.keys()):
                return self._covered[message_id]

        self._signal_names |= needed
        await self._call_from(message_id)
        return self._covered[message_id]

    async def _call_from(self, message_id: int) -> None:
        turn_ids = self._turns_from(message_id)
        signal_definition = Signals(lambda: self._automaton, self._db).get_definition(self._signal_names)

        tag_specs: list[tuple[str, str]] = []
        for i, _turn_id in enumerate(turn_ids, start=1):
            tag_specs.append((f'signals{i}', 'signals'))
            tag_specs.append((f'env{i}', 'env'))

        seed_env = self._seed_env(message_id)
        base_prompt = f"{self._automaton.general_prompt}\n\n{BATCH_TAG_INSTRUCTIONS}"
        base_prompt = f"{base_prompt}\n\nStarting env (read-only context):\n{Env(stored=seed_env).serialise_as_text()}"
        if signal_definition:
            base_prompt = f"{base_prompt}\n\n{signal_definition}"

        protocol_cls = TurnProtocolUsingSchema if self._ai_service.is_provider_with_schema() else TurnProcotolUsingTextExtraction
        protocol = protocol_cls(self._ai_service, True)

        chat_history = self._build_chat_history(turn_ids[-1])

        tag_kind_by_name = dict(tag_specs)
        by_index: dict[int, dict] = {}

        def on_metadata(tag: str, value: str) -> None:
            kind = tag_kind_by_name.get(tag)
            if kind is None:
                return
            index = int(tag[len(kind):])
            entry = by_index.setdefault(index, {'signals': {}, 'env': {}})
            if kind == 'signals':
                entry['signals'].update(MetadataHandler.parse_raw_signals(value))
            elif kind == 'env':
                entry['env'].update(MetadataHandler.parse_raw_env(value))

        async for _ in protocol.generate_reply_with_schema(base_prompt, tag_specs, chat_history, on_metadata):
            pass

        for i, turn_id in enumerate(turn_ids, start=1):
            entry = by_index.get(i, {'signals': {}, 'env': {}})
            self._covered[turn_id] = (entry['signals'], entry['env'])
        self.batch_segments += 1

    def _seed_env(self, message_id: int) -> dict:
        all_user_message_ids = self._user_message_ids()
        index = all_user_message_ids.index(message_id)
        previous_id = all_user_message_ids[index - 1] if index > 0 else None
        if previous_id is not None and previous_id in self._covered:
            return self._covered[previous_id][1]

        session = self._db.get_chat_session(self._session_id)
        if session is None or session['datetime_start'] is None:
            return {}
        persisted_env = PersistedEnv(
            self._db, get_username=lambda: session['username'], get_active_project_name=lambda: session['project_name'],
        )
        return persisted_env.stored(until=session['datetime_start'])

    def _user_message_ids(self) -> list[int]:
        return [m['id'] for m in self._db.get_messages(self._session_id) if m['role'] == 'user']

    def _turns_from(self, message_id: int) -> list[int]:
        return [mid for mid in self._user_message_ids() if mid >= message_id]

    def _build_chat_history(self, up_to_message_id: int) -> list[dict]:
        messages = self._db.get_messages(self._session_id)
        return [{"role": m["role"], "content": m["content"]} for m in messages if m["id"] <= up_to_message_id]
