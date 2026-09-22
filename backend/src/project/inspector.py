from __future__ import annotations

from typing import Any

from automaton.automaton import Action, Automaton, ProjectPayload, State, StatePayload
from automaton.file_types import media_doc_id_for
from automaton.identifier_registry import IdentifierRegistry
from automaton.trigger_namespaces import TriggerNamespaces
from db import Db
from system.web_session import WebSession
from tracking.sources import driver_class_for

from .archive.automaton_loader import AutomatonLoader
from .health import ProjectHealthChecker, broken_fields
from .archive.layout import CACHE_DIR, LEGAL_TERMS_FILE_NAME


class ProjectInspector:
    def __init__(self, db: Db, automaton_loader: AutomatonLoader, ai_service: Any = None) -> None:
        self._db = db
        self._automaton_loader = automaton_loader
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
        return self.state_recorded_as(project_id, automaton, state_key)

    def state_recorded_as(self, project_id: str, automaton: Automaton, state_key: str | None) -> State:
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

    def has_ever_run(self, project_id: str, username: str, type: str = 'live') -> bool:
        return self._db.get_current_state_for_user(project_id, username, type=type) is not None

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
        pending = accepted_id != current.id and (
            accepted_id is None or self._db.get_archive_content_by_id(accepted_id) != current.content
        )
        return {"pending": pending, "content": current.content.decode("utf-8") if pending else None}

    def legal_terms_pending(self, username: str, project_id: str, revision: int | None = None) -> bool:
        return self.get_legal_terms_status(username, project_id, revision)["pending"]

    def get_automaton(self, project_id: str, revision: int) -> Automaton:
        return self._automaton_loader.load_at_revision(project_id, revision)

    def get_draft_automaton(self, project_id: str) -> Automaton:
        """The live draft, always interpreted — a compiled package is
        only ever a snapshot of a *published* revision, and the point of
        the draft is to answer for an edit that has not been built yet.
        `load_at_revision(project_id, get_draft_revision(project_id))`
        cannot tell "I want the draft" from "I want whatever revision
        this number happens to be" — right after a publish the two
        numbers are equal, and a compiled package for it would be served
        by mistake — so this goes through `load`, the one seam
        CompiledAutomatonLoader itself forces back to interpreted for
        exactly this reason."""
        return self._automaton_loader.load(project_id)

    def get_automaton_and_state(
        self, project_id: str, type: str = 'live', username: str | None = None
    ) -> tuple[Automaton, State]:
        automaton = (
            self.get_draft_automaton(project_id) if type == 'test'
            else self.get_automaton(project_id, self.get_published_revision(project_id))
        )
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
            return self.get_draft_automaton(project_id)
        return self.get_automaton(project_id, session["project_revision"])

    def get_automaton_and_state_for_session(self, session_id: int) -> tuple[Automaton, State]:
        session = self._db.get_chat_session(session_id)
        if session is None:
            raise FileNotFoundError(f"Session {session_id} does not exist.")
        automaton = self.get_automaton_for_session(session_id)
        return automaton, self._resolve_state(session["project_id"], automaton, session_id=session_id)

    def get_automaton_and_state_as_recorded(self, session_id: int, state_key: str | None) -> tuple[Automaton, State]:
        session = self._db.get_chat_session(session_id)
        if session is None:
            raise FileNotFoundError(f"Session {session_id} does not exist.")
        automaton = self.get_automaton_for_session(session_id)
        return automaton, self.state_recorded_as(session["project_id"], automaton, state_key)

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
        project_id = self._db.get_active_project_id(WebSession().user)
        if project_id is None:
            raise FileNotFoundError("No project is currently active.")
        return project_id

    def resolve_manual_action(self, action_name: str, session_id: int) -> tuple[StatePayload, Action, str]:
        """Resolves a manual (button) action against the session's current
        state and returns the destination state's payload, the Action, and
        the source state's key (e.g. to detect a self-loop). Nothing is
        recorded here: the transition lands through TrackingEngine, the
        same way a triggered one does."""
        automaton, state = self.get_automaton_and_state_for_session(session_id)
        action = automaton.move(state.key, action_name)
        return automaton.get_state_payload(automaton.get_state(action.target)), action, state.key

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
        Inspect panel. `relevant` is what a turn in `state_key` computes
        (its `signal-tracking-strategy`) when given, or every state's combined otherwise."""
        automaton = self._automaton_loader.load_at_revision(
            project_id, self._resolve_inspector_revision(project_id, session_id)
        )
        if state_key is not None and state_key in automaton.states:
            relevant_names = automaton.tracked_signal_names(state_key)
        else:
            relevant_names = automaton.all_tracked_signal_names()
        return [
            {
                "signal": Automaton.get_signal_payload(signal),
                "relevant": signal.name in relevant_names,
                "attachments": list(signal.attachments),
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

    def get_project_sources(self, project_id: str, session_id: int | None = None) -> list[dict]:
        """Source declarations of `project_id`'s index.yml, for the
        Inspect panel Source card — same revision contract as get_project_signals."""
        automaton = self._automaton_loader.load_at_revision(
            project_id, self._resolve_inspector_revision(project_id, session_id)
        )
        return [{"source": Automaton.get_source_payload(source)} for source in automaton.sources]

    def get_project_metadata(self, project_id: str) -> ProjectPayload:
        """The `project:` section of `project_id`'s last saved index.yml,
        read off the already-built Automaton rather than re-parsing the YAML."""
        automaton = self._automaton_loader.load(project_id)
        return {
            "id": automaton.project_id,
            "family": automaton.family,
            "revision": automaton.project_revision,
            "ui_label": automaton.project_ui_label,
            "ui_description": automaton.project_ui_description,
            "services": automaton.services.as_raw(),
            "signal_tracking_on_ai_message": automaton.autotracking_on_ai_message,
            "new_session_strategy": automaton.new_session_strategy,
            "general_prompt": automaton.general_prompt,
        }

    def get_identifier_registry(self, project_id: str) -> dict[str, dict[str, str]]:
        """Every identifier a trigger/`env:` expression can reference:
        signals, env keys, `source.<name>`, `media.<doc_id>`, and
        whatever namespace an installed contributor declares. Reads the
        unpublished draft."""
        automaton = self._automaton_loader.load(project_id)
        registry = IdentifierRegistry.build(automaton.signals, automaton.env_keys)
        registry["source"] = {}
        for source in automaton.sources:
            try:
                descriptions = driver_class_for(source.url).METHOD_DESCRIPTIONS
            except (ValueError, KeyError):
                descriptions = {}
            registry[f"source.{source.name}"] = dict(descriptions)
        registry["media"] = {}
        for name in self._db.list_archives(project_id):
            doc_id = media_doc_id_for(name)
            if doc_id is not None:
                registry[f"media.{doc_id}"] = {
                    "url": f"Returns the download url for '{name}' — e.g. media.{doc_id}.url()."
                }
        registry.update(TriggerNamespaces.collect().identifiers(automaton))
        return registry

    def get_project_states(self, project_id: str) -> list[str]:
        """Every real state key of `project_id`'s current draft
        automaton, excluding the reserved "" pseudo-state."""
        automaton = self._automaton_loader.load(project_id)
        return [state.key for state in automaton.states.values() if state.key != ""]

    def get_state_input_tokens(self, project_id: str, state_key: str, session_id: int | None = None) -> int | None:
        """Estimated input-token cost of `state_key`'s own turn prompt,
        fetched on demand per state rather than folded into
        get_project_graph (one estimate call per state on every load)."""
        if self._ai_service is None:
            return None
        revision = self._resolve_inspector_revision(project_id, session_id)
        automaton = self._automaton_loader.load_at_revision(project_id, revision)
        if state_key not in automaton.states:
            raise ValueError(f"Project '{project_id}' has no state '{state_key}'.")
        state = automaton.get_state(state_key)
        from tracking.project_files import project_files_for
        from turn.input_processor import processors
        prompt = processors()[state.input_processor].estimate(
            automaton, state, project_files_for(self._db, automaton),
        )
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
                "signal_tracking_strategy": state.signal_tracking_strategy,
                "ai_memory_scope": state.ai_memory_scope,
                "attachments": list(state.attachments),
                "contextual_prompt": state.contextual_prompt,
            }
            for state in real_states
        ]
        edges = [
            {
                "action": Automaton.get_action_payload(action),
                "source": state.key,
                "trigger": action.trigger,
                "ui_description": action.ui_description,
                "env": action.env or {},
            }
            for state in automaton.states.values()
            for action in state.actions
        ]
        return {
            "nodes": nodes, "edges": edges, "autotracking_on_ai_message": automaton.autotracking_on_ai_message,
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
        """{revision, published_revision, is_paused, paused_reason,
        broken, modified_files} for the "Edit project" toolbar's revision
        display, refreshed after every save and publish. `broken` says
        which of the two revisions does not build, because a paused
        project whose *draft* is fine is asking to be published, not
        edited — and a reason line that only says "no longer builds"
        reads as a complaint about the file on screen, which is the one
        that works."""
        if project_id not in self._db.list_projects():
            raise FileNotFoundError(f"Project '{project_id}' does not exist.")
        is_paused, paused_reason = self._db.get_project_availability(project_id) or (False, None)
        revision = self._db.get_project_revision(project_id)
        published_revision = self._db.get_project_published_revision(project_id)
        return {
            "revision": revision,
            "published_revision": published_revision,
            "is_paused": is_paused,
            "paused_reason": paused_reason,
            "broken": broken_fields(ProjectHealthChecker(self._db, self._automaton_loader).current(project_id)),
            "modified_files": self._modified_archive_names(project_id, revision, published_revision),
        }

    def _modified_archive_names(self, project_id: str, revision: int, published_revision: int | None) -> list[str]:
        if published_revision is None or revision == published_revision:
            return []
        current = self._db.get_archive_hashes(project_id, revision=revision)
        published = self._db.get_archive_hashes(project_id, revision=published_revision)
        return [
            name for name, file_hash in current.items()
            if not name.startswith(f"{CACHE_DIR}/") and published.get(name) != file_hash
        ]
