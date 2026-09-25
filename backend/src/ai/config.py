from __future__ import annotations

from pathlib import Path

from config import (
    ConfigError, optional_non_negative_float, optional_positive_int, optional_section, provider_prefix, providers,
    require_str,
)
from ai.llm_provider import AIServiceConfig

SECTION = "ai-service"

_MODES = ("live", "test")
_NO_AUTO_MODE = "no-auto"
_VALID_MODES = _MODES + (_NO_AUTO_MODE,)


def _parse_modes(entry: dict, i: int, path: Path) -> tuple[str, ...]:
    """None (the key absent entirely) means both live and test — the
    default every entry had before `modes` existed at all. An explicit
    empty list is different: it deliberately puts the entry in neither
    cascade, rather than falling back to that default."""
    modes = entry.get("modes")
    if modes is None:
        return _MODES
    if not isinstance(modes, list) or not all(isinstance(m, str) for m in modes):
        raise ConfigError(f"{path}: '{SECTION}.providers[{i}].modes' must be a list of strings if present.")
    invalid = sorted(set(modes) - set(_VALID_MODES))
    if invalid:
        raise ConfigError(
            f"{path}: '{SECTION}.providers[{i}].modes' contains invalid entr{'y' if len(invalid) == 1 else 'ies'} "
            f"{invalid} — must be 'live', 'test', and/or 'no-auto'."
        )
    return tuple(dict.fromkeys(modes))


def parse(raw: dict, path: Path) -> list[AIServiceConfig] | None:
    """None when the whole `ai-service` section is absent — no build-time
    reason to require an LLM provider, since a project can run purely
    through a human operator (see turn/input_processor.py's `system`
    processor). Present, it works exactly as before: a non-empty
    providers list, each mode's cascade left non-empty."""
    if raw.get(SECTION) is None:
        return None
    entries = providers(raw, SECTION, path)
    sub = optional_section(raw, SECTION, path)
    max_output_tokens = optional_positive_int(sub, SECTION, "max-output-tokens", path, 4096)

    services = []
    for i, entry in enumerate(entries):
        prefix = provider_prefix(entry, SECTION, i, path)
        url = entry.get("url")
        driver = require_str(entry, prefix, "driver", path)
        model = require_str(entry, prefix, "model", path)
        key = entry.get("key")
        if not isinstance(key, str):
            raise ConfigError(f"{path}: '{prefix}.key' must be a string.")
        ui_label = entry.get("ui-label")
        ui_label = ui_label.strip() if isinstance(ui_label, str) and ui_label.strip() else driver
        ui_description = entry.get("ui-description")
        ui_description = ui_description.strip() if isinstance(ui_description, str) and ui_description.strip() else None
        modes = _parse_modes(entry, i, path)
        token_budget_per_day = optional_positive_int(entry, prefix, "token-budget-per-day", path, 1_000_000)
        services.append(AIServiceConfig(
            driver=driver, model=model, key=key, url=url,
            ui_label=ui_label, ui_description=ui_description,
            max_output_tokens=max_output_tokens, modes=modes,
            token_budget_per_day=token_budget_per_day,
            input_token_ppm=optional_non_negative_float(entry, prefix, "input-token-ppm", path, 0.0),
            output_token_ppm=optional_non_negative_float(entry, prefix, "output-token-ppm", path, 0.0),
            thought_token_ppm=optional_non_negative_float(entry, prefix, "thought-token-ppm", path, 0.0),
        ))
    for mode in _MODES:
        matching = [service for service in services if mode in service.modes]
        if not matching:
            raise ConfigError(f"{path}: '{SECTION}.providers' has no entry left for mode {mode!r}.")
        if all(_NO_AUTO_MODE in service.modes for service in matching):
            raise ConfigError(
                f"{path}: '{SECTION}.providers' has no entry left for mode {mode!r} that isn't "
                f"{_NO_AUTO_MODE!r} — its auto cascade would be empty."
            )
    return services


def public_fields(configs: list[AIServiceConfig] | None) -> dict:
    if not configs:
        return {"enabled": False}
    return {
        "enabled": True,
        "max-output-tokens": configs[0].max_output_tokens,
        "providers": [
            {
                "driver": p.driver, "model": p.model, "ui-label": p.ui_label, "ui-description": p.ui_description,
                "url": p.url, "modes": list(p.modes), "token-budget-per-day": p.token_budget_per_day,
                "input-token-ppm": p.input_token_ppm, "output-token-ppm": p.output_token_ppm,
                "thought-token-ppm": p.thought_token_ppm,
            }
            for p in configs
        ],
    }
