"""The one place a trigger/`env:`-expression evaluation scope gets
assembled — every reserved namespace, plus every core metric as a bare
top-level name. Constructed once and shared by every caller that needs
this scope: TrackingEngine's auto-tracking trigger-eval/env-apply paths,
and the live what-if preview off already-known
signal values."""
from __future__ import annotations

import datetime
from typing import Any, TYPE_CHECKING

from simpleeval import ModuleWrapper

from automaton.automaton import Automaton
from automaton.scope import EvaluationScope
from db import Db
from metrics.metric_service import MetricService
from tracking.actuators import AttachmentNamespace, ChatNamespace, FakeChatNamespace, FakeTaskNamespace, TaskNamespace
from tracking.env import Env
from tracking.project_files import project_files_for
from tracking.evaluator import SignalEvaluator
from tracking.session_facts import SessionFacts
from tracking.sources import SourceNamespace
from tracking.sources.websearch import websearch_archive_for
from tracking.user_facts import UserFacts

if TYPE_CHECKING:
    from tracking.automaton_namespace import AutomatonNamespace
    from ai import AiService


class EvaluationScopeBuilder(object):
    def __init__(
        self,
        env: Env,
        metrics: MetricService,
        session: SessionFacts,
        user: UserFacts,
        db: Db,
        automaton_namespace: "AutomatonNamespace | None" = None,
        task_namespace: TaskNamespace | None = None,
        chat_namespace: ChatNamespace | None = None,
        ai_service: "AiService | None" = None,
    ) -> None:
        self._env = env
        self._metrics = metrics
        self._session = session
        self._user = user
        self._db = db
        self._automaton_namespace = automaton_namespace
        self._task_namespace = task_namespace if task_namespace is not None else FakeTaskNamespace()
        self._chat_namespace = chat_namespace if chat_namespace is not None else FakeChatNamespace()
        self._ai_service = ai_service

    def build(
        self, automaton: Automaton, state_key: str, raw_signal_values: dict[str, Any] | None,
        session_id: int | None = None, output_values: dict[str, Any] | None = None,
    ) -> EvaluationScope:
        """`raw_signal_values` is always re-coerced against every declared
        signal, never assumed pre-validated. `output_values` is this turn's
        own freshly generated structured output (see tracking.prompt.
        OutputPrompt) — filtered here to `state_key`'s own declared
        `output` names and merged onto the `env` namespace, so a trigger
        evaluated the same turn a state produces output already sees the
        new value, ahead of TrackingProcessor.process's own persisted
        copy-back. env/session/user/source/attachment/metric are cheap,
        lazy proxies included unconditionally
        (attachment.read is only ever reachable from task — see
        IdentifierRegistry.TRIGGER_SCOPE_EXCLUDES — but nothing stops it
        being present for trigger/env too, the same as `task`/`chat`
        already are); only the bare core-metric names are gated, since
        building them is eager. `source`/`attachment` are rebuilt fresh every call
        (unlike env/session/user, never threaded through __init__) since
        they need `automaton` itself — a `build()` parameter, not a
        constructor dependency any caller has to wire up separately — to
        know where to actually read from (see Automaton.
        set_storage_location). `session_id`: the firing chat session,
        forwarded to SourceNamespace for its own per-session read cache
        (tracking.sources.avance_archive) — None outside a real chat
        session (a wake-up re-evaluation, a test replay), where a source
        driver just reads its canonical archive directly instead."""
        signal_values = SignalEvaluator().validate(automaton, raw_signal_values)
        source_namespace = SourceNamespace(self._db, automaton, session_id, env=self._env)
        project_files = project_files_for(self._db, automaton)
        state = automaton.states.get(state_key)
        output_for_env = {
            name: value for name, value in (output_values or {}).items() if state is not None and name in state.output
        }
        scope: dict[str, Any] = {
            "signal": signal_values,
            "env": {**self._env.action_set(), **output_for_env},
            "session": self._session,
            "user": self._user.as_dict(),
            "source": source_namespace,
            "attachment": AttachmentNamespace(project_files, automaton),
            "metric": self._metrics.for_turn(),
            # FIXME: simpleeval rejects a raw module ("modules are not allowed") — ModuleWrapper is its
            "datetime": ModuleWrapper(datetime, allowed_attrs={"datetime", "timedelta", "timezone"}),
        }
        if self._automaton_namespace is not None:
            scope["automaton"] = self._automaton_namespace.scoped_to(automaton.family)
        scope["chat"] = self._chat_namespace
        task_namespace = self._task_namespace.with_services(automaton.services).with_websearch_archive(
            websearch_archive_for(self._db, automaton)
        )
        if self._ai_service is not None:
            tool_set = (
                source_namespace.tool_set(
                    state.ai_may_read_sources, state.ai_must_read_sources, state.ai_may_write_sources,
                )
                if state is not None and state.ai_source_names else None
            )
            scope["task"] = task_namespace.with_ai_service(self._ai_service, tool_set=tool_set)
        else:
            scope["task"] = task_namespace
        merged = self._metrics.merge_if_referenced(automaton, state_key, scope)
        return EvaluationScope(merged, automaton=automaton, state_key=state_key)
