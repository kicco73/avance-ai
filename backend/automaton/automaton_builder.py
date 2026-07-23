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
    """Builds an Automaton from a model's index.yml: parses the YAML,
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
                    f"{field_description}: attachment '{rel_path}' not found in {base_dir}"
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
        # lives, not a shared fixed directory — each model carries its own.
        base_dir = path.parent

        initial_state = raw["initial_state"]
        general_prompt = raw["general_prompt"].strip()
        general_prompt_attachments = self._load_attachments(
            raw.get("attachments", []), "general_prompt", base_dir
        )
        raw_states = raw["states"]

        states: dict[str, State] = {}
        for key, raw_state in raw_states.items():
            actions = [
                Action(
                    name=raw_action["name"],
                    label=raw_action["label"],
                    button_text=raw_action["button_text"],
                    target=raw_action["target"],
                    trigger=raw_action.get("trigger"),
                )
                for raw_action in raw_state.get("actions", [])
            ]
            fixed_message = raw_state.get("fixed_message")
            states[key] = State(
                key=key,
                label=raw_state["label"],
                # Derived, not read from YAML: a state is final iff it has no
                # outgoing actions. Keeps the flag structurally impossible to
                # desync from the actual `actions` list.
                final=len(actions) == 0,
                description=raw_state["description"].strip(),
                on_enter=raw_state["on_enter"] if "on_enter" in raw_state else None,
                contextual_prompt=raw_state["contextual_prompt"],
                actions=actions,
                fixed_message=fixed_message.strip() if fixed_message else None,
                transition_log_level=raw_state.get("transition_log_level", "WARNING"),
                attachments=self._load_attachments(raw_state.get("attachments", []), f"state '{key}'", base_dir),
            )

        signals: list[Signal] = []
        seen_signal_names: set[str] = set()
        for raw_signal in raw.get("signals", []):
            name = raw_signal["name"]
            if name in seen_signal_names:
                raise ValueError(f"Duplicate signal name '{name}' in 'signals'")
            seen_signal_names.add(name)
            signals.append(
                Signal(
                    name=name,
                    ui_label=raw_signal["ui_label"],
                    description=raw_signal["description"].strip(),
                    ai_prompt=raw_signal["ai_prompt"].strip(),
                    placeholder_builtin=raw_signal.get("placeholder_builtin", False),
                    attachments=self._load_attachments(
                        raw_signal.get("attachments", []), f"signal '{name}'", base_dir
                    ),
                )
            )

        if initial_state not in states:
            raise ValueError(f"initial_state '{initial_state}' is not defined among the states")

        for state in states.values():
            if state.transition_log_level not in VALID_LOG_LEVELS:
                raise ValueError(
                    f"State '{state.key}': transition_log_level "
                    f"'{state.transition_log_level}' must be one of {sorted(VALID_LOG_LEVELS)}"
                )
            for action in state.actions:
                if action.target not in states:
                    raise ValueError(
                        f"State '{state.key}', action '{action.name}': "
                        f"target '{action.target}' is not a valid state"
                    )
                if action.trigger:
                    try:
                        referenced_names = trigger_signal_names(action.trigger)
                    except SyntaxError as exc:
                        raise ValueError(
                            f"State '{state.key}', action '{action.name}': "
                            f"trigger '{action.trigger}' is not a valid expression: {exc}"
                        ) from exc
                    unknown_names = referenced_names - seen_signal_names
                    if unknown_names:
                        raise ValueError(
                            f"State '{state.key}', action '{action.name}': "
                            f"trigger references undefined signal(s): {', '.join(sorted(unknown_names))}"
                        )

        return Automaton(
            initial_state=initial_state,
            states=states,
            general_prompt=general_prompt,
            signals=signals,
            general_prompt_attachments=general_prompt_attachments,
        )