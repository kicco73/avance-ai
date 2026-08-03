"""TurnProcessor: the responsibility of turning one user text message
into a full chat turn — session bookkeeping, pre/post auto-tracking (see
_run_auto_tracking), asking the active TurnStrategy for the AI's own
reply (see chat.turn_strategy_builder.build_turn_strategy), and building
the turn's own response shape. Deliberately separate from ChatService
itself, which instead owns *when* a turn should even run (see its own
process_turn — locking/mutual-exclusion against a concurrent manual
action is a cross-cutting concern shared with apply_manual_action, not
part of "creating a turn" itself) plus everything a turn shares with
other flows (session/message ownership checks, building a turn's own
raw prompt parts, generating an opening/action-prompt message — see
ChatService's own _require_active_session/_build_turn_prompt_parts/
_history_cutoff/_messages_for_transition, injected here as plain
callables rather than duplicated).

automaton.autotracking_on_user_message no longer means "make a separate,
dedicated AI call before the real reply just to get signals" (see
tracking.evaluator.SignalEvaluator/chat.turn_strategy.TurnStrategy.
compute_explicitly for that older, now-unused-for-this-purpose
mechanism) — it's optimistic instead (see process()): generate the real
reply first, using the *current* state's own context, getting its
signals for free the same way autotracking_on_ai_message already does.
The common case (no transition) needed exactly one call anyway. Only
when that guess turns out wrong (a transition actually fires) is the
just-generated reply thrown away — it was built with the wrong state's
context — and regenerated once more, this time with the new state's
own context; from there on this is a perfectly ordinary reply like any
other, no special-casing needed (autotracking_on_ai_message, if also
on, evaluates its own freshly-computed signals exactly as it always
does). Known limitation, deferred to a separate session: any audio
metadata already pushed live for the discarded reply (see
chat.turn_strategy_v2.TurnStrategyV2's own on_metadata "audio" branch)
has already reached the frontend by the time the transition is known
about.
"""
from __future__ import annotations

from datetime import datetime
from http import HTTPStatus
from typing import Awaitable, Callable

from ai.ai_service import AiService, OnRetry
from automaton.automaton import Action, Automaton, State, StatePayload
from chat.env import Env
from chat.errors import ChatServiceError
from chat.priming import build_priming_messages
from chat.session_manager import ChatSessionManager
from chat.turn_callbacks import OnChunk, OnMetadata
from chat.turn_strategy_builder import build_turn_strategy
from db import Db
from tracking.tracking_service import TrackingService

RequireActiveSession = Callable[[int | None, str, str], dict]
BuildTurnPromptParts = Callable[[Automaton, State], tuple[str, "str | None", list]]
HistoryCutoff = Callable[[str, State], "datetime | None"]
MessagesForTransition = Callable[..., Awaitable[list[dict]]]
GetActiveAutomatonAndState = Callable[[], tuple[Automaton, State]]
GetActiveProjectName = Callable[[], str]


class TurnProcessor:
    def __init__(
        self,
        ai_service: AiService,
        db: Db,
        tracking_service: TrackingService,
        session_manager: ChatSessionManager,
        env: Env,
        get_active_automaton_and_state: GetActiveAutomatonAndState,
        get_active_project_name: GetActiveProjectName,
        require_active_session: RequireActiveSession,
        build_turn_prompt_parts: BuildTurnPromptParts,
        history_cutoff: HistoryCutoff,
        messages_for_transition: MessagesForTransition,
    ) -> None:
        self._ai_service = ai_service
        self._db = db
        self._tracking_service = tracking_service
        self._session_manager = session_manager
        self.env = env
        self._get_active_automaton_and_state = get_active_automaton_and_state
        self._get_active_project_name = get_active_project_name
        self._require_active_session = require_active_session
        self._build_turn_prompt_parts = build_turn_prompt_parts
        self._history_cutoff = history_cutoff
        self._messages_for_transition = messages_for_transition

    @staticmethod
    def _strip_timestamps(history: list[dict]) -> list[dict]:
        """`LLMProvider.generate` only knows {role, content} — timestamps
        are kept in the persisted conversation for /api/signals, not sent
        to the model during normal chat. Duplicated from ChatService's
        own copy (see its own docstring) rather than injected: trivial,
        pure, and zero-dependency enough that sharing one instance would
        buy nothing."""
        return [{"role": m["role"], "content": m["content"]} for m in history]

    @staticmethod
    def _current_state_payload(automaton: Automaton, state: State) -> StatePayload:
        return automaton.get_state_payload(state)

    async def _run_auto_tracking(
        self,
        pending_message: dict | None,
        project_name: str,
        session_id: int,
        automaton: Automaton,
        state: State,
        signal_values: dict | None,
    ) -> tuple[Action | None, State, list[dict], int | None]:
        """The trailing `int | None` is the id of whatever Tracking row
        this call's own evaluation persisted (None if auto-tracking is
        off, or this state has nothing triggerable to evaluate at all —
        see TrackingService.run_auto_tracking) — the caller links it to
        the message that caused this call, once that message itself has
        an id (see process()/_finish_turn/link_signal_to_message)."""
        action, new_state, signal_row_id = await self._tracking_service.run_auto_tracking(
            pending_message, project_name, session_id, automaton, state, signal_values
        )
        if action is None:
            return None, state, [], signal_row_id

        messages = await self._messages_for_transition(
            action, project_name, session_id, automaton, new_state, is_self_loop=(action.target == state.key)
        )
        return action, new_state, messages, signal_row_id

    async def process(
        self,
        text: str,
        session_id: int | None,
        on_retry: OnRetry | None,
        on_chunk: OnChunk | None,
        on_metadata: OnMetadata | None,
    ) -> dict:
        automaton, state, project_name, resolved_session_id, user_message_id = await self._begin_turn(
            text, session_id
        )

        messages: list[dict] = []
        action: Action | None = None
        skip_ai_message_tracking = False

        if automaton.autotracking_on_user_message:
            # Optimistic guess: generate the real reply first, using the
            # *current* state's own context (see this module's own
            # docstring) — the common case (no transition) needed exactly
            # this one call anyway.
            reply, audio_text, signal_values, env_updates = await self._generate_reply(
                automaton, state, project_name, resolved_session_id, on_retry, on_chunk, on_metadata
            )
            guessed_action, new_state, transition_messages, signal_row_id = await self._run_auto_tracking(
                None, project_name, resolved_session_id, automaton, state, signal_values
            )
            if signal_row_id is not None:
                self._db.link_signal_to_message(signal_row_id, user_message_id)

            if guessed_action is not None:
                # Wrong guess — the reply just generated belongs to the
                # state the user was in, not the one auto-tracking just
                # moved to. Thrown away and regenerated once more below,
                # with the new state's own context this time.
                action = guessed_action
                state = new_state
                messages.extend(transition_messages)
                if not state.chat:
                    # Same early exit as ever for a transition landing on
                    # a state that doesn't accept chat at all — nothing
                    # left to regenerate a reply for.
                    self._session_manager.touch_session(resolved_session_id, state.key)
                    return self._build_turn_response(
                        automaton, state, resolved_session_id, messages, action, user_message_id, None
                    )
                reply, audio_text, signal_values, env_updates = await self._generate_reply(
                    automaton, state, project_name, resolved_session_id, on_retry, on_chunk, on_metadata
                )
                # From here on this is an entirely ordinary reply, freshly
                # computed against the new state's own context —
                # autotracking_on_ai_message below (if also on) evaluates
                # it exactly like it would for any other turn.
            else:
                # The guess paid off — already validated and persisted
                # against the user's own message above; nothing left for
                # autotracking_on_ai_message below to redundantly
                # recompute off the very same numbers.
                skip_ai_message_tracking = True
        else:
            reply, audio_text, signal_values, env_updates = await self._generate_reply(
                automaton, state, project_name, resolved_session_id, on_retry, on_chunk, on_metadata
            )

        return await self._finish_turn(
            automaton, state, project_name, resolved_session_id, messages, action, user_message_id,
            reply, audio_text, signal_values, env_updates, skip_ai_message_tracking=skip_ai_message_tracking,
        )

    async def _begin_turn(
        self, text: str, session_id: int | None
    ) -> tuple[Automaton, State, str, int, int]:
        """Validates the active state accepts chat, resolves the active
        session, and saves the user's own message — the one thing every
        turn needs regardless of which auto-tracking mode (if any) ends
        up active (see process() for what happens next). The trailing
        `int` is the user's own just-saved message id (see
        _build_turn_response's own "user_message_id" — a live/streaming
        caller has no other way to learn it, since it creates its own
        local bubble optimistically, before this call even starts)."""

        automaton, state = self._get_active_automaton_and_state()

        if not state.chat:
            raise ChatServiceError(
                "This state doesn't accept messages; use an action instead.", status_code=HTTPStatus.CONFLICT
            )

        project_name = self._get_active_project_name()
        session = self._require_active_session(session_id, project_name, state.key)
        resolved_session_id = session["id"]

        user_message_id = self._db.save_message("user", text, resolved_session_id)

        return automaton, state, project_name, resolved_session_id, user_message_id

    def _build_turn_response(
        self, automaton: Automaton, state: State, resolved_session_id: int, messages: list[dict], action: Action | None,
        user_message_id: int, assistant_message_id: int | None,
    ) -> dict:
        """The turn's own response shape — shared by every generation
        mode, and also by the early-exit case where pre-turn auto-
        tracking already moved to a state that doesn't accept chat (see
        _finish_turn's own caller), which never gets as far as an actual
        AI reply at all (assistant_message_id is None exactly then).
        Both ids let a live/streaming caller correlate `reply`'s own
        entries against messages it already created locally/optimistically
        (see frontend chatStore.js's own submitMessage/handleSend) —
        `reply` itself carries an id per message, but not which one (if
        any) is *the* live chat reply already being streamed into a
        placeholder bubble vs. a separate transition message (an
        action_prompt/opening message, from either autotracking_on_user_
        message above or autotracking_on_ai_message below) that needs its
        own new bubble instead."""
        return {
            "reply": messages,
            "user_message_id": user_message_id,
            "assistant_message_id": assistant_message_id,
            "state": self._current_state_payload(automaton, state),
            "state_changed": action is not None,
            "new_state": action.target if action else None,
            "triggered_action": action.name if action else None,
            # The fired action's own on_enter (see automaton.Action.
            # on_enter) — None both when no transition happened this turn
            # and when the action that did fire simply has none set.
            # Kebab-cased key, matching the YAML field's own spelling.
            "on-enter": action.on_enter if action else None,
            # See ChatService.apply_manual_action's own "ai_model" for why
            # this rides along with the turn's result instead of a
            # separate call.
            "ai_model": self._ai_service.get_models_info(),
            "session_id": resolved_session_id,
        }

    async def _finish_turn(
        self,
        automaton: Automaton,
        state: State,
        project_name: str,
        resolved_session_id: int,
        messages: list[dict],
        action: Action | None,
        user_message_id: int,
        reply: str,
        audio_text: str | None,
        signal_values: dict | None,
        env_updates: dict,
        *,
        skip_ai_message_tracking: bool = False,
    ) -> dict:
        """Everything after the AI reply text (and its audio/signals/env,
        however they were actually obtained — see chat.turn_strategy.
        TurnStrategy.generate_reply's own return shape) is known: persists
        env, saves the assistant's own message, runs post-turn auto-
        tracking (see automaton.autotracking_on_ai_message) — unless
        skip_ai_message_tracking (see process()'s own optimistic
        autotracking_on_user_message path: `signal_values` here would be
        the exact same ones already validated/persisted against the
        user's own message moments ago; re-running the same evaluation
        against them again would be pure redundant work) — and builds
        the turn's own response."""
        self.env.update(env_updates)
        assistant_id = self._db.save_message("assistant", reply, resolved_session_id, audio_text=audio_text)
        messages.append({"id": assistant_id, "content": reply, "audio_text": audio_text})

        if automaton.autotracking_on_ai_message and not skip_ai_message_tracking:
            last_action, state, transition_messages, signal_row_id = await self._run_auto_tracking(
                None, project_name, resolved_session_id, automaton, state, signal_values
            )
            if last_action:
                action = last_action
            messages.extend(transition_messages)
            if signal_row_id is not None:
                self._db.link_signal_to_message(signal_row_id, assistant_id)

        self._session_manager.touch_session(resolved_session_id, state.key)
        return self._build_turn_response(
            automaton, state, resolved_session_id, messages, action, user_message_id, assistant_id
        )

    async def _generate_reply(
        self,
        automaton: Automaton,
        state: State,
        project_name: str,
        resolved_session_id: int,
        on_retry: OnRetry | None,
        on_chunk: OnChunk | None,
        on_metadata: OnMetadata | None,
    ) -> tuple[str, str | None, dict | None, dict]:
        """Builds this turn's own raw prompt parts/chat history (see
        ChatService._build_turn_prompt_parts — needs automaton/state/
        tracking_service, ChatService's own dependencies, hence injected
        rather than duplicated here), then delegates to whichever
        TurnStrategy this turn should use (see chat.turn_strategy_builder.
        build_turn_strategy) both the final system-prompt assembly *and*
        the AI call itself — each strategy's own metadata section differs
        by provider capability (see chat.turn_strategy.TurnStrategy's own
        module docstring for why), so the prompt can't be fully built
        here, only handed over in parts."""
        base_prompt, signal_definition, turn_attachments = self._build_turn_prompt_parts(automaton, state)

        priming_messages = build_priming_messages(turn_attachments)
        since = self._history_cutoff(project_name, state)
        chat_history = priming_messages + self._strip_timestamps(
            self._db.get_messages(resolved_session_id, since=since)
        )

        strategy = build_turn_strategy(self._ai_service, wants_streaming=on_chunk is not None)
        return await strategy.generate_reply(
            base_prompt, signal_definition, self.env, chat_history, on_retry, on_chunk, on_metadata
        )
