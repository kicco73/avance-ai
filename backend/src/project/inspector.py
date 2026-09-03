from __future__ import annotations

from typing import TYPE_CHECKING

from automaton.automaton import Action, Automaton, ProjectPayload, State, StatePayload
from automaton.automaton_builder import family_of
from automaton.identifier_registry import IdentifierRegistry
from db import Db
from session import Session
from tracking.tracking_engine import TrackingEngine

from .archive.automaton_loader import AutomatonLoader
from .archive.layout import LEGAL_TERMS_FILE_NAME

if TYPE_CHECKING:
    from ai.ai_service import AiService


class ProjectInspector:
    def __init__(self, db: Db, automaton_loader: AutomatonLoader, ai_service: "AiService | None" = None) -> None:
        self._db = db
        self._automaton_loader = automaton_loader
        # Optional: only needed for get_project_graph's per-state input-
        # token estimate — every other method here works without it, so
        # tests that don't care about that estimate can omit it.
        self._ai_service = ai_service

    def _resolve_state(
        self, project_id: str, automaton: Automaton, *, session_id: int | None = None, type: str | None = None,
        username: str | None = None,
    ) -> State:
        """No persisted state yet falls back to init_action.target. A
        persisted state that no longer exists means a publish renamed or
        removed it — only StateRemap (written at that publish) may resolve it."""
        if session_id is not None:
            state_key = self._db.get_current_state_for_session(session_id)
        elif username is not None:
            state_key = self._db.get_current_state_for_user(project_id, username, type=type)
        else:
            state_key = self._db.get_current_state(project_id, type=type)
        if state_key is None:
            state_key = automaton.init_action.target
        elif state_key not in automaton.states:
            remapped = self._db.get_state_remap(project_id, state_key)
            if remapped is None or remapped not in automaton.states:
                raise ValueError(
                    f"Project '{project_id}': persisted state '{state_key}' no longer exists "
                    "and has no StateRemap entry — this should have been caught at publish time."
                )
            state_key = remapped
        return automaton.get_state(state_key)

    def get_published_revision(self, project_id: str) -> int:
        published_revision = self._db.get_project_published_revision(project_id)
        if published_revision is None:
            raise ValueError(f"Project '{project_id}' has never been published.")
        return published_revision

    def get_draft_revision(self, project_id: str) -> int:
        return self._db.get_project_revision(project_id)

    def get_legal_terms_status(self, username: str, project_id: str, revision: int | None = None) -> dict:
        current = self._db.get_archive_row(project_id, LEGAL_TERMS_FILE_NAME, revision=revision)
        if current is None:
            return {"pending": False, "content": None}
        accepted_id = self._db.get_accepted_terms_archive_id(username, project_id)
        pending = accepted_id != current.id
        return {"pending": pending, "content": current.content.decode("utf-8") if pending else None}

    def legal_terms_pending(self, username: str, project_id: str, revision: int | None = None) -> bool:
        return self.get_legal_terms_status(username, project_id, revision)["pending"]

    def get_automaton(self, project_id: str, revision: int) -> Automaton:
        return self._automaton_loader.load_at_revision(project_id, revision)

    def get_automaton_and_state(
        self, project_id: str, type: str = 'live', username: str | None = None
    ) -> tuple[Automaton, State]:
        revision = self.get_published_revision(project_id) if type == 'live' else self.get_draft_revision(project_id)
        automaton = self.get_automaton(project_id, revision)
        return automaton, self._resolve_state(project_id, automaton, type=type, username=username)

    def get_active_automaton(self) -> Automaton:
        project_id = self.get_active_project_id()
        if project_id is None:
            raise FileNotFoundError("No project is currently active.")
        return self.get_automaton(project_id, self.get_published_revision(project_id))

    def get_active_automaton_and_state(self, username: str | None = None) -> tuple[Automaton, State]:
        """The active project's published automaton and state — never the
        in-progress draft. A caller with a concrete session_id uses
        get_automaton_and_state_for_session instead."""
        project_id = self.get_active_project_id()
        if project_id is None:
            raise FileNotFoundError("No project is currently active.")
        return self.get_automaton_and_state(project_id, username=username)

    def get_automaton_for_session(self, session_id: int) -> Automaton:
        """The Automaton `session_id`'s turns must run against. A native
        session is pinned to the revision published when it was created;
        a 'test' session always re-resolves against the live draft."""
        session = self._db.get_chat_session(session_id)
        if session is None:
            raise FileNotFoundError(f"Session {session_id} does not exist.")
        project_id = session["project_id"]
        if session["type"] == "test":
            return self._automaton_loader.load(project_id)
        return self.get_automaton(project_id, session["project_revision"])

    def get_automaton_and_state_for_session(self, session_id: int) -> tuple[Automaton, State]:
        session = self._db.get_chat_session(session_id)
        if session is None:
            raise FileNotFoundError(f"Session {session_id} does not exist.")
        automaton = self.get_automaton_for_session(session_id)
        return automaton, self._resolve_state(session["project_id"], automaton, session_id=session_id)

    def get_automaton_and_state_for_observer(
        self, project_id: str, username: str
    ) -> tuple[Automaton, State] | None:
        """`project_id`'s published Automaton and State, as seen by
        `username`. Returns None, never raises, when `username` has no
        session — unlike a nonexistent `project_id`, which raises FileNotFoundError."""
        if not self._db.project_exists(project_id):
            raise FileNotFoundError(f"Project '{project_id}' does not exist.")
        session = self._db.get_latest_chat_session(username, project_id)
        if session is None:
            return None
        automaton = self._automaton_loader.load_at_revision(project_id, session["project_revision"])
        return automaton, self._resolve_state(project_id, automaton, session_id=session["id"])

    def get_active_project_id(self) -> str:
        """The current session user's active project id, read fresh from
        the DB every time. Raises if nothing is active, e.g. never
        activated anything or the active project was since deleted."""
        project_id = self._db.get_active_project_id(Session().user)
        if project_id is None:
            raise FileNotFoundError("No project is currently active.")
        return project_id

    def apply_manual_action(self, action_name: str, session_id: int) -> tuple[StatePayload, Action, str]:
        """Applies a manual (button) action and returns the destination
        state's payload, the Action that fired, and the source state's
        key (e.g. to detect a self-loop)."""
        automaton, state = self.get_automaton_and_state_for_session(session_id)
        action = automaton.move(state.key, action_name)
        new_state = automaton.get_state(action.target)
        # Always saved, self-loop or not: a self-loop just never counts
        # toward history_cutoff.
        self._db.save_transition(
            state.key,
            action_name,
            new_state.key,
            session_id,
            transition_log_level=new_state.transition_log_level,
            origin='manual',
        )
        # This path writes save_transition directly rather than going through
        # TrackingEngine.apply_transition, so it must publish explicitly.
        session = self._db.get_chat_session(session_id)
        assert session is not None  # already resolved by get_automaton_and_state_for_session above
        TrackingEngine.notify_transition(session["username"], session["project_id"], state.key, new_state.key)
        return automaton.get_state_payload(new_state), action, state.key

    def get_active_state_payload(self) -> StatePayload:
        automaton, state = self.get_active_automaton_and_state()
        return automaton.get_state_payload(state)

    def _resolve_inspector_revision(self, project_id: str, session_id: int | None) -> int:
        """The revision an Inspect-panel read should read `project_id`
        at. Mirrors get_automaton_and_state_for_session's own resolution,
        so reviewing an older session never shows today's structure."""
        if session_id is None:
            return self._db.get_project_revision(project_id)
        session = self._db.get_chat_session(session_id)
        if session is None:
            raise FileNotFoundError(f"Session {session_id} does not exist.")
        if session["type"] == "test":
            return self._db.get_project_revision(project_id)
        return session["project_revision"]

    def get_project_signals(
        self, project_id: str, state_key: str | None = None, session_id: int | None = None
    ) -> list[dict]:
        """Signal definitions of `project_id`'s index.yml, for the
        Inspect panel. `relevant` scopes to `state_key`'s outgoing
        actions when given, or every state's triggers combined otherwise."""
        automaton = self._automaton_loader.load_at_revision(
            project_id, self._resolve_inspector_revision(project_id, session_id)
        )
        if state_key is not None and state_key in automaton.states:
            relevant_names = automaton.triggerable_signal_names(state_key)
        else:
            relevant_names = automaton.all_triggerable_signal_names()
        return [
            {
                "signal": Automaton.get_signal_payload(signal),
                "relevant": signal.name in relevant_names,
                # Not part of SignalPayload itself — filenames only, never
                # full content.
                "attachments": [a.filename for a in signal.attachments.values()],
            }
            for signal in automaton.signals
        ]

    def get_project_env_keys(self, project_id: str, session_id: int | None = None) -> list[dict]:
        """Env-key declarations of `project_id`'s index.yml, for the
        Inspect panel Env tab — same revision contract as get_project_signals."""
        automaton = self._automaton_loader.load_at_revision(
            project_id, self._resolve_inspector_revision(project_id, session_id)
        )
        return [{"env_key": Automaton.get_env_key_payload(env_key)} for env_key in automaton.env_keys]

    def get_project_metadata(self, project_id: str) -> ProjectPayload:
        """The `project:` section of `project_id`'s last saved index.yml,
        read off the already-built Automaton rather than re-parsing the YAML."""
        automaton = self._automaton_loader.load(project_id)
        return {
            "id": automaton.project_id,
            "revision": automaton.project_revision,
            "ui_label": automaton.project_ui_label,
            "ui_description": automaton.project_ui_description,
            "talk_enabled": automaton.talk_enabled,
            "signal_tracking_on_ai_message": automaton.autotracking_on_ai_message,
            "general_prompt": automaton.general_prompt,
        }

    def get_identifier_registry(self, project_id: str) -> dict[str, dict[str, str]]:
        """Every identifier `project_id`'s trigger/`env:` expressions can
        reference, plus an "automaton.<id>"/"automaton.<id>.env" entry per
        *other* project in the same family (see automaton_builder.
        family_of) — a project outside it is never offered here, same
        boundary AutomatonLoader.known_projects_env_keys enforces at build
        time and AutomatonNamespace enforces at runtime. Only ever called
        from the design view (TriggerEditor's autocomplete), so — like
        get_project_signals/get_project_env_keys — this reads the
        in-progress draft, not the published revision: a signal/env key
        just declared must be offerable before the project is published."""
        automaton = self._automaton_loader.load(project_id)
        registry = IdentifierRegistry.build(automaton.signals, automaton.env_keys)
        registry["automaton"] = {}
        family = family_of(project_id)
        for other_id in self._db.list_projects():
            if other_id == project_id or family_of(other_id) != family:
                continue
            registry[f"automaton.{other_id}"] = {"state": f"The '{other_id}' project's own current state."}
            try:
                other_automaton = self._automaton_loader.load(other_id)
            except Exception:  # noqa: BLE001 — still offerable via .state, just without its own env keys
                env_keys = {}
            else:
                env_keys = {env_key.name: env_key.ui_description or "" for env_key in other_automaton.env_keys}
            registry[f"automaton.{other_id}.env"] = env_keys
        return registry

    def get_project_states(self, project_id: str) -> list[str]:
        """Every real state key of `project_id`'s current draft
        automaton, excluding the reserved "" pseudo-state."""
        automaton = self._automaton_loader.load(project_id)
        return [state.key for state in automaton.states.values() if state.key != ""]

    def get_state_input_tokens(self, project_id: str, state_key: str, session_id: int | None = None) -> int | None:
        """Estimated input-token cost of `state_key`'s own turn prompt
        (see tracking_processor.estimate_state_prompt), for the Inspect
        panel's detail card — deliberately its own call, fetched on demand
        for whichever single state is open, rather than folded into
        get_project_graph: that would cost one estimate call per state on
        every graph load. None when no AiService was wired in."""
        if self._ai_service is None:
            return None
        revision = self._resolve_inspector_revision(project_id, session_id)
        automaton = self._automaton_loader.load_at_revision(project_id, revision)
        if state_key not in automaton.states:
            raise ValueError(f"Project '{project_id}' has no state '{state_key}'.")
        state = automaton.get_state(state_key)
        # Deferred: tracking.tracking_processor imports tracking.definitions,
        # which imports project.project_service, which imports this very
        # module — a top-level import here would be circular.
        from tracking.tracking_processor import estimate_state_prompt
        prompt = estimate_state_prompt(self._ai_service, automaton, state)
        return self._ai_service.get_input_tokens(prompt)

    def get_project_graph(self, project_id: str, session_id: int | None = None) -> dict:
        """The project's state machine as nodes (states) and edges
        (actions). The reserved "" state is excluded from `nodes` but
        `edges` still includes its init_action as a `source: ""` edge."""
        revision = self._resolve_inspector_revision(project_id, session_id)
        automaton = self._automaton_loader.load_at_revision(project_id, revision)
        real_states = [state for state in automaton.states.values() if state.key != ""]
        nodes = [
            {
                "state": automaton.get_state_payload(state),
                "is_start": state.key == automaton.init_action.target,
                "history_cutoff": state.history_cutoff,
                "reactions_enabled": state.reactions_enabled,
                "transition_log_level": state.transition_log_level,
                "attachments": list(state.attachments.keys()),
                # Not part of StatePayload — a state's system-prompt text
                # never reaches a live chat client, only this Inspect panel.
                "contextual_prompt": state.contextual_prompt,
            }
            for state in real_states
        ]
        edges = [
            {
                "action": Automaton.get_action_payload(action),
                "source": state.key,
                # None of these four belong in ActionPayload — each is
                # internal transition logic that never reaches a live
                # chat client, this Inspect panel's own concern only.
                "trigger": action.trigger,
                "action_prompt": action.action_prompt,
                "ui_description": action.ui_description,
                "env": action.env or {},
            }
            for state in automaton.states.values()
            for action in state.actions
        ]
        return {
            "nodes": nodes, "edges": edges, "autotracking_on_ai_message": automaton.autotracking_on_ai_message,
            # The exact revision this graph was actually built from — lets
            # the "Rev. X" badge stay accurate without a second fetch.
            "revision": revision,
        }

    def list_projects(self, username: str | None = None) -> dict:
        projects = (
            self._db.list_projects_with_availability()
            if username is None
            else self._db.list_projects_with_availability_for_user(username)
        )
        try:
            active = self.get_active_project_id()
        except FileNotFoundError:
            active = None
        return {"projects": projects, "active": active}

    def get_project_revision_info(self, project_id: str) -> dict:
        """{revision, published_revision, is_paused, paused_reason} for the
        "Edit project" toolbar's revision display, refreshed after every
        save and publish."""
        if project_id not in self._db.list_projects():
            raise FileNotFoundError(f"Project '{project_id}' does not exist.")
        is_paused, paused_reason = self._db.get_project_availability(project_id) or (False, None)
        return {
            "revision": self._db.get_project_revision(project_id),
            "published_revision": self._db.get_project_published_revision(project_id),
            "is_paused": is_paused,
            "paused_reason": paused_reason,
        }
