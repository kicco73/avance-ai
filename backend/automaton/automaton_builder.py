from automaton.automaton import Action, Attachment, Automaton, Signal, State, trigger_signal_names


import yaml


import base64
from pathlib import Path

EXTENSION_TO_MEDIA_TYPE = {
    ".md": "text/plain",
    ".txt": "text/plain",
    ".csv": "text/plain",
    ".pdf": "application/pdf",
}

VALID_LOG_LEVELS = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}

class AutomatonBuilder(object):
    """Builds an Automaton from a project's index.yml: parses the YAML,
    resolves attachments, validates the result, and constructs the
    Automaton — the one place that shape is decided."""

    @staticmethod
    def _load_attachments(paths: list[str], field_description: str, base_dir: Path) -> list[Attachment]:
        """Reads attachment files once per build() call, resolved relative
        to `base_dir` (the YAML file's own directory). Raises ValueError
        for an unsupported extension or a missing file."""
        attachments = []
        for rel_path in paths:
            extension = Path(rel_path).suffix.lower()
            if extension not in EXTENSION_TO_MEDIA_TYPE:
                raise ValueError(
                    f"{field_description}: attachment '{rel_path}' has unsupported extension "
                    f"'{extension}'. Supported: {sorted(EXTENSION_TO_MEDIA_TYPE)}"
                )
            full_path = base_dir / rel_path
            if not full_path.is_file():
                raise ValueError(
                    f"{field_description}: attachment '{rel_path}' not found in {base_dir.name}"
                )
            media_type = EXTENSION_TO_MEDIA_TYPE[extension]
            if media_type == "text/plain":
                source = {
                    "type": "text",
                    "media_type": "text/plain",
                    "data": full_path.read_text(encoding="utf-8"),
                }
            else:
                encoded = base64.b64encode(full_path.read_bytes()).decode("ascii")
                source = {"type": "base64", "media_type": media_type, "data": encoded}
            attachments.append(Attachment(filename=rel_path, source=source))
        return attachments

    def build(self, path: str | Path) -> Automaton:
        path = Path(path)
        with open(path, "r", encoding="utf-8") as f:
            raw = yaml.safe_load(f)

        # Attachments are resolved relative to wherever this specific YAML file
        # lives, not a shared fixed directory — each project carries its own.
        base_dir = path.parent

        general_prompt = raw["general_prompt"].strip()
        general_prompt_attachments = self._load_attachments(
            raw.get("attachments", []), "general_prompt", base_dir
        )
        autotracking_on_user_message = raw.get("signal_tracking_on_user_message", True)
        autotracking_on_ai_message = raw.get("signal_tracking_on_ai_message", False)
        raw_states = raw["states"]
        if not isinstance(raw_states, dict):
            raise ValueError(f"'states' must be a mapping of state name -> fields, got {type(raw_states).__name__}.")
        if "" in raw_states:
            raise ValueError(
                "State '' is reserved for the implicit initial state (see init_action) "
                "and cannot be declared in 'states'."
            )

        raw_init_action = raw.get("init_action")
        if not isinstance(raw_init_action, dict) or not raw_init_action.get("target"):
            raise ValueError(
                "'init_action' is required and must be a mapping with at least a 'target' "
                "field — the project's real starting state."
            )
        init_action = Action(
            name="init_action",
            label="init_action",
            ui_button="",
            target=raw_init_action["target"],
            action_prompt=raw_init_action["action_prompt"].strip() if raw_init_action.get("action_prompt") else None,
        )

        states: dict[str, State] = {}
        for key, raw_state in raw_states.items():
            if not isinstance(raw_state, dict):
                raise ValueError(
                    f"State '{key}': expected a mapping of fields (label, description, "
                    f"actions, ...), got {type(raw_state).__name__} instead. This usually "
                    "means a field meant to belong to a state (e.g. 'actions') was "
                    "indented as a sibling of the state's key rather than nested under it, "
                    "so YAML parsed it as its own separate state."
                )
            actions = [
                Action(
                    name=raw_action["name"],
                    label=raw_action["label"],
                    ui_button=raw_action["ui_button"],
                    # Missing 'target' means a self-loop: the action stays
                    # on the state it fired from.
                    target=raw_action.get("target", key),
                    trigger=raw_action.get("trigger"),
                    action_prompt=raw_action["action_prompt"].strip() if raw_action.get("action_prompt") else None,
                )
                for raw_action in raw_state.get("actions", [])
            ]
            fixed_message = raw_state.get("fixed_message")
            contextual_prompt = raw_state.get("contextual_prompt")
            if fixed_message and contextual_prompt is not None:
                raise ValueError(
                    f"State '{key}': 'fixed_message' and 'contextual_prompt' are mutually "
                    "exclusive — a fixed_message state never generates free-form content, "
                    "so it has no use for a contextual_prompt."
                )
            if not fixed_message and contextual_prompt is None:
                raise ValueError(f"State '{key}': 'contextual_prompt' is required unless 'fixed_message' is set.")

            states[key] = State(
                key=key,
                label=raw_state["label"],
                # Derived, not read from YAML: a state is final iff it has no
                # outgoing actions. Keeps the flag structurally impossible to
                # desync from the actual `actions` list.
                final=len(actions) == 0,
                description=raw_state["description"].strip() if raw_state.get("description") else None,
                on_enter=raw_state["on_enter"] if "on_enter" in raw_state else None,
                contextual_prompt=contextual_prompt.strip() if contextual_prompt else None,
                actions=actions,
                fixed_message=fixed_message.strip() if fixed_message else None,
                transition_log_level=raw_state.get("transition_log_level", "WARNING"),
                attachments=self._load_attachments(raw_state.get("attachments", []), f"state '{key}'", base_dir),
                history_cutoff=raw_state.get("history_cutoff", False),
                chat=raw_state.get("chat", True),
            )

        raw_signals = raw.get("signals", {})
        if not isinstance(raw_signals, dict):
            raise ValueError(f"'signals' must be a mapping of signal name -> fields, got {type(raw_signals).__name__}.")

        signals: list[Signal] = []
        seen_signal_names: set[str] = set()
        for name, raw_signal in raw_signals.items():
            seen_signal_names.add(name)
            signals.append(
                Signal(
                    name=name,
                    ui_label=raw_signal["ui_label"],
                    description=raw_signal["description"].strip() if raw_signal.get("description") else None,
                    definition=raw_signal["definition"].strip(),
                    attachments=self._load_attachments(
                        raw_signal.get("attachments", []), f"signal '{name}'", base_dir
                    ),
                )
            )

        # Minimal, never declared in YAML — see the reserved-key check
        # above. Not a real conversational state: ChatService.open_if_needed
        # is the only place that ever resolves out of it, via init_action.
        states[""] = State(key="", label="", final=False, description="")

        for state in states.values():
            if state.transition_log_level not in VALID_LOG_LEVELS:
                raise ValueError(
                    f"State '{state.key}': transition_log_level "
                    f"'{state.transition_log_level}' must be one of {sorted(VALID_LOG_LEVELS)}"
                )

        # init_action validated the same way as every other action's target/
        # trigger, just with a synthetic ("", init_action) entry alongside
        # every real state's own actions rather than a separate check.
        actions_by_state = [(state.key, action) for state in states.values() for action in state.actions]
        actions_by_state.append(("", init_action))
        for state_key, action in actions_by_state:
            if action.target not in states:
                raise ValueError(
                    f"State '{state_key}', action '{action.name}': "
                    f"target '{action.target}' is not a valid state"
                )
            if action.trigger:
                try:
                    referenced_names = trigger_signal_names(action.trigger)
                except SyntaxError as exc:
                    raise ValueError(
                        f"State '{state_key}', action '{action.name}': "
                        f"trigger '{action.trigger}' is not a valid expression: {exc}"
                    ) from exc
                unknown_names = referenced_names - seen_signal_names
                if unknown_names:
                    raise ValueError(
                        f"State '{state_key}', action '{action.name}': "
                        f"trigger references undefined signal(s): {', '.join(sorted(unknown_names))}"
                    )

        return Automaton(
            init_action=init_action,
            states=states,
            general_prompt=general_prompt,
            signals=signals,
            general_prompt_attachments=general_prompt_attachments,
            autotracking_on_user_message=autotracking_on_user_message,
            autotracking_on_ai_message=autotracking_on_ai_message,
        )