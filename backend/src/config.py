from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from ruamel.yaml import YAML

from ai.llm_provider import AIServiceConfig

_yaml = YAML(typ='rt')

class ConfigError(Exception):
    """Raised when backend/.config.yml is missing or structurally invalid."""

@dataclass(frozen=True)
class TalkServiceConfig:
    driver: str
    model: str
    # Optional: a future local provider (e.g. Piper) won't need one.
    key: str | None
    # Optional: falls back to `driver` (see AppConfig._parse_talk_services).
    ui_label: str
    ui_description: str | None = None


@dataclass(frozen=True)
class ListenServiceConfig:
    driver: str
    model: str
    # Optional: unused by faster-whisper, kept for a future remote provider.
    key: str | None
    # Optional: skips faster-whisper's autodetect when given (e.g. "ca").
    language: str | None
    # Optional: falls back to `driver` (see AppConfig._parse_listen_services).
    ui_label: str
    ui_description: str | None = None


class AppConfig:

    VALID_TRANSPORTS = ("websocket", "rest")
    CONFIG_PATHS = [
        Path(__file__).resolve().parent / ".config.yml",
        Path('/etc/secrets') / "avance.yml",
    ]

    @classmethod
    def _load_yml(cls):
        for path in cls.CONFIG_PATHS:
            if not path.is_file():
                continue
            with path.open("r", encoding="utf-8") as f:
                raw = _yaml.load(f)
                return raw, path
        return None, None

    @staticmethod
    def _require_str(raw: dict, section: str, field: str, path: Path) -> str:
        sub = raw.get(section)
        if not isinstance(sub, dict):
            raise ConfigError(f"{path}: '{section}' section is missing or not a mapping.")
        value = sub.get(field)
        if not isinstance(value, str) or not value.strip():
            raise ConfigError(f"{path}: '{section}.{field}' is missing or empty.")
        return value.strip()

    @staticmethod
    def _get_section(raw: dict, section: str, path: Path) -> dict:
        sub = raw.get(section)
        if not isinstance(sub, dict):
            raise ConfigError(f"{path}: '{section}' section is missing or not a mapping.")
        return sub

    @staticmethod
    def _get_optional_section(raw: dict, section: str, path: Path) -> dict:
        """Like _get_section, but an absent section is treated as empty
        rather than an error — for optional sections (e.g. `jobs`) that may
        be omitted entirely, unlike required sections such as chat-service."""
        sub = raw.get(section, {})
        if not isinstance(sub, dict):
            raise ConfigError(f"{path}: '{section}' section is not a mapping.")
        return sub

    @classmethod
    def _get_optional_positive_float(
        cls, raw: dict, section: str, field: str, path: Path, default: float
    ) -> float:
        sub = cls._get_section(raw, section, path)
        value = sub.get(field, default)
        if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
            raise ConfigError(f"{path}: '{section}.{field}' must be a positive number if present.")
        return float(value)

    @classmethod
    def _get_optional_positive_int(
        cls, raw: dict, section: str, field: str, path: Path, default: int
    ) -> int:
        sub = cls._get_optional_section(raw, section, path)
        value = sub.get(field, default)
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            raise ConfigError(f"{path}: '{section}.{field}' must be a positive integer if present.")
        return value

    @classmethod
    def _get_optional_bool(cls, raw: dict, section: str, field: str, path: Path, default: bool) -> bool:
        sub = cls._get_section(raw, section, path)
        value = sub.get(field, default)
        if not isinstance(value, bool):
            raise ConfigError(f"{path}: '{section}.{field}' must be a boolean if present.")
        return value

    @classmethod
    def _get_providers(cls, raw: dict, section: str, path: Path) -> list:
        sub = cls._get_section(raw, section, path)
        entries = sub.get("providers")
        if not isinstance(entries, list) or not entries:
            raise ConfigError(f"{path}: '{section}.providers' must be a non-empty list.")
        return entries

    @staticmethod
    def _parse_ui_fields(entry: dict, driver: str, section: str, i: int, path: Path) -> tuple[str, str | None]:
        """ui-label falls back to `driver` when absent/blank; ui-description
        stays None when absent. Shared by the three providers[] parsers."""
        ui_label = entry.get("ui-label")
        if ui_label is not None and not isinstance(ui_label, str):
            raise ConfigError(f"{path}: '{section}.providers[{i}].ui-label' must be a string if present.")
        ui_description = entry.get("ui-description")
        if ui_description is not None and not isinstance(ui_description, str):
            raise ConfigError(f"{path}: '{section}.providers[{i}].ui-description' must be a string if present.")
        return (ui_label.strip() if ui_label and ui_label.strip() else driver), \
            (ui_description.strip() if ui_description and ui_description.strip() else None)

    @classmethod
    def _get_optional_providers(cls, raw: dict, section: str, path: Path) -> list | None:
        """None if the whole section is absent or `section.enabled` is
        falsy, meaning the caller skips the service (off by default).
        Otherwise the same non-empty providers list as _get_providers."""
        sub = raw.get(section)
        if sub is None:
            return None
        if not isinstance(sub, dict):
            raise ConfigError(f"{path}: '{section}' section is not a mapping.")
        enabled = sub.get("enabled", False)
        if not isinstance(enabled, bool):
            raise ConfigError(f"{path}: '{section}.enabled' must be a boolean.")
        if not enabled:
            return None
        entries = sub.get("providers")
        if not isinstance(entries, list) or not entries:
            raise ConfigError(f"{path}: '{section}.providers' must be a non-empty list.")
        return entries

    @classmethod
    def _parse_talk_services(cls, raw: dict, path: Path) -> list[TalkServiceConfig] | None:
        entries = cls._get_optional_providers(raw, "talk-service", path)
        if entries is None:
            return None

        services = []
        for i, entry in enumerate(entries):
            if not isinstance(entry, dict):
                raise ConfigError(f"{path}: 'talk-service.providers[{i}]' must be a mapping.")
            driver = entry.get("driver")
            model = entry.get("model")
            key = entry.get("key")
            if not isinstance(driver, str) or not driver.strip():
                raise ConfigError(f"{path}: 'talk-service.providers[{i}].driver' is missing or empty.")
            if not isinstance(model, str) or not model.strip():
                raise ConfigError(f"{path}: 'talk-service.providers[{i}].model' is missing or empty.")
            if key is not None and not isinstance(key, str):
                raise ConfigError(f"{path}: 'talk-service.providers[{i}].key' must be a string if present.")
            driver = driver.strip()
            ui_label, ui_description = cls._parse_ui_fields(entry, driver, "talk-service", i, path)
            services.append(TalkServiceConfig(
                driver=driver, model=model.strip(), key=key, ui_label=ui_label, ui_description=ui_description,
            ))
        return services

    @classmethod
    def _parse_listen_services(cls, raw: dict, path: Path) -> list[ListenServiceConfig] | None:
        entries = cls._get_optional_providers(raw, "listen-service", path)
        if entries is None:
            return None

        services = []
        for i, entry in enumerate(entries):
            if not isinstance(entry, dict):
                raise ConfigError(f"{path}: 'listen-service.providers[{i}]' must be a mapping.")
            driver = entry.get("driver")
            model = entry.get("model")
            key = entry.get("key")
            language = entry.get("language")
            if not isinstance(driver, str) or not driver.strip():
                raise ConfigError(f"{path}: 'listen-service.providers[{i}].driver' is missing or empty.")
            if not isinstance(model, str) or not model.strip():
                raise ConfigError(f"{path}: 'listen-service.providers[{i}].model' is missing or empty.")
            if key is not None and not isinstance(key, str):
                raise ConfigError(f"{path}: 'listen-service.providers[{i}].key' must be a string if present.")
            if language is not None and not isinstance(language, str):
                raise ConfigError(f"{path}: 'listen-service.providers[{i}].language' must be a string if present.")
            driver = driver.strip()
            ui_label, ui_description = cls._parse_ui_fields(entry, driver, "listen-service", i, path)
            services.append(ListenServiceConfig(
                driver=driver, model=model.strip(), key=key,
                language=language.strip() if language else None,
                ui_label=ui_label, ui_description=ui_description,
            ))
        return services

    @classmethod
    def _parse_ai_services(cls, raw: dict, path: Path) -> list[AIServiceConfig]:
        entries = cls._get_providers(raw, "ai-service", path)

        services = []
        for i, entry in enumerate(entries):
            if not isinstance(entry, dict):
                raise ConfigError(f"{path}: 'ai-service.providers[{i}]' must be a mapping.")
            driver = entry.get("driver")
            model = entry.get("model")
            key = entry.get("key")
            url = entry.get("url")
            if not isinstance(driver, str) or not driver.strip():
                raise ConfigError(f"{path}: 'ai-service.providers[{i}].driver' is missing or empty.")
            if not isinstance(model, str) or not model.strip():
                raise ConfigError(f"{path}: 'ai-service.providers[{i}].model' is missing or empty.")
            if not isinstance(key, str):
                raise ConfigError(f"{path}: 'ai-service.providers[{i}].key' must be a string.")
            if key is not None and not isinstance(key, str):
                raise ConfigError(f"{path}: 'ai-service.providers[{i}].key' must be a string or None.")
            driver = driver.strip()
            ui_label, ui_description = cls._parse_ui_fields(entry, driver, "ai-service", i, path)
            services.append(AIServiceConfig(
                driver=driver, model=model.strip(), key=key, url=url,
                ui_label=ui_label, ui_description=ui_description,
            ))
        return services

    def __init__(self) -> None:

        raw, path = self._load_yml()
        if not isinstance(raw, dict):
            raise ConfigError(f"{path} must contain a YAML mapping at the top level.")

        self.database_url = self._require_str(raw, "database", "url", path)
        # Off by default: this is destructive, wiping and rebuilding a
        # stale/incompatible schema automatically instead of leaving it
        # for a human to migrate or restore by hand.
        self.database_force_drop_and_create_when_incompatible = self._get_optional_bool(
            raw, "database", "force-drop-and-create-when-incompatible", path, default=False
        )
        self.chat_transport = self._require_str(raw, "chat-service", "transport", path)
        if self.chat_transport not in self.VALID_TRANSPORTS:
            raise ConfigError(
                f"{path}: chat-service.transport={self.chat_transport!r} is not "
                f"valid. Allowed values: {', '.join(self.VALID_TRANSPORTS)}."
            )
        # The single source of truth for how long a chat session stays
        # "open" (see chat/session_manager.py's ChatSessionManager) — never
        # hardcoded elsewhere.
        self.max_session_duration_in_minutes = self._get_optional_positive_float(
            raw, "chat-service", "max_session_duration_in_minutes", path, default=60.0
        )

        # Two independent worker pools (see jobs/job_queue.py's JobQueue),
        # one per JobSink implementation, never shared between them. Both
        # optional, and so is the whole `jobs` section.
        self.jobs_max_concurrent_persisted = self._get_optional_positive_int(
            raw, "jobs", "max_concurrent_persisted", path, default=2
        )
        self.jobs_max_concurrent_ephemeral = self._get_optional_positive_int(
            raw, "jobs", "max_concurrent_ephemeral", path, default=4
        )

        self.ai_services = self._parse_ai_services(raw, path)
        self.talk_services = self._parse_talk_services(raw, path)
        self.listen_services = self._parse_listen_services(raw, path)


