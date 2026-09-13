from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit
from ruamel.yaml import YAML

from system import bus
from ai import AIServiceConfig
from system.bus import POINT_CONFIG_SERVICES
from system.config_services import ui_section
DEFAULT_APPS_DIR = Path(__file__).resolve().parent.parent / "apps"



def _redact_database_url(url: str) -> str:
    """Same url .config.yml carries, minus embedded credentials (a
    mysql:// url can hold a plaintext user:pass) — this is what the
    frontend's Settings > Manage services > Database tab actually shows
    (see AppConfig.public_services_snapshot)."""
    parsed = urlsplit(url)
    if not parsed.username and not parsed.password:
        return url
    netloc = parsed.hostname or ""
    if parsed.port:
        netloc += f":{parsed.port}"
    return urlunsplit((parsed.scheme, netloc, parsed.path, parsed.query, parsed.fragment))

class ConfigError(Exception):
    """Raised when backend/.config.yml is missing or structurally invalid."""

@dataclass(frozen=True)
class BuildServiceConfig:
    """The optional `build-service` section: credentials for the git
    remote (e.g. GitHub) the Build wizard pushes/pulls a project's
    source from, plus where built packages live. Every credential
    optional — the wizard can be opened and the repository step filled in
    before any credential exists."""
    repo_url: str | None
    username: str | None
    token: str | None
    apps_dir: Path


@dataclass(frozen=True)
class AuthProviderConfig:
    driver: str
    key: str
    ui_label: str
    ui_description: str | None = None


class AppConfig:

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
                raw = YAML(typ='rt').load(f)
                return raw, path
        return None, None

    @staticmethod
    def _require_str_in(sub: dict, section: str, field: str, path: Path) -> str:
        value = sub.get(field)
        if not isinstance(value, str) or not value.strip():
            raise ConfigError(f"{path}: '{section}.{field}' is missing or empty.")
        return value.strip()

    @staticmethod
    def _optional_str_in(sub: dict, section: str, field: str, path: Path) -> str | None:
        value = sub.get(field)
        if value is not None and not isinstance(value, str):
            raise ConfigError(f"{path}: '{section}.{field}' must be a string if present.")
        return value

    @classmethod
    def _require_str(cls, raw: dict, section: str, field: str, path: Path) -> str:
        return cls._require_str_in(cls._get_section(raw, section, path), section, field, path)

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
        be omitted entirely, unlike required sections such as turn-service."""
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

    @staticmethod
    def _positive_int_in(sub: dict, section: str, field: str, path: Path, default: int) -> int:
        value = sub.get(field, default)
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            raise ConfigError(f"{path}: '{section}.{field}' must be a positive integer if present.")
        return value

    @staticmethod
    def _non_negative_int_in(sub: dict, section: str, field: str, path: Path, default: int) -> int:
        value = sub.get(field, default)
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ConfigError(f"{path}: '{section}.{field}' must be a non-negative integer if present.")
        return value

    @staticmethod
    def _choice_in(sub: dict, section: str, field: str, path: Path, default: str, choices: tuple[str, ...]) -> str:
        value = sub.get(field, default)
        if value not in choices:
            raise ConfigError(f"{path}: '{section}.{field}' must be one of {', '.join(choices)} if present.")
        return value

    @classmethod
    def _get_optional_positive_int(
        cls, raw: dict, section: str, field: str, path: Path, default: int
    ) -> int:
        return cls._positive_int_in(cls._get_optional_section(raw, section, path), section, field, path, default)

    @classmethod
    def _get_optional_non_negative_int(
        cls, raw: dict, section: str, field: str, path: Path, default: int
    ) -> int:
        return cls._non_negative_int_in(cls._get_optional_section(raw, section, path), section, field, path, default)

    @classmethod
    def _get_optional_choice(cls, raw: dict, section: str, field: str, path: Path, default: str, choices: tuple[str, ...]) -> str:
        return cls._choice_in(cls._get_section(raw, section, path), section, field, path, default, choices)

    @classmethod
    def _get_providers(cls, raw: dict, section: str, path: Path) -> list:
        sub = cls._get_section(raw, section, path)
        entries = sub.get("providers")
        if not isinstance(entries, list) or not entries:
            raise ConfigError(f"{path}: '{section}.providers' must be a non-empty list.")
        return entries

    @staticmethod
    def _provider_prefix(entry: object, section: str, i: int, path: Path) -> str:
        if not isinstance(entry, dict):
            raise ConfigError(f"{path}: '{section}.providers[{i}]' must be a mapping.")
        return f"{section}.providers[{i}]"

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
    def _parse_auth_providers(cls, raw: dict, path: Path) -> list[AuthProviderConfig]:
        entries = cls._get_providers(raw, "auth-service", path)

        providers = []
        for i, entry in enumerate(entries):
            prefix = cls._provider_prefix(entry, "auth-service", i, path)
            driver = cls._require_str_in(entry, prefix, "driver", path)
            key = cls._require_str_in(entry, prefix, "key", path)
            ui_label, ui_description = cls._parse_ui_fields(entry, driver, "auth-service", i, path)
            providers.append(AuthProviderConfig(
                driver=driver, key=key, ui_label=ui_label, ui_description=ui_description,
            ))
        return providers

    @classmethod
    def _parse_build_service_config(cls, raw: dict, path: Path) -> BuildServiceConfig:
        sub = cls._get_optional_section(raw, "build-service", path)
        for field in ("repo-url", "username", "token", "apps-dir"):
            cls._optional_str_in(sub, "build-service", field, path)
        apps_dir = (sub.get("apps-dir") or "").strip()
        return BuildServiceConfig(
            repo_url=(sub.get("repo-url") or "").strip() or None,
            username=(sub.get("username") or "").strip() or None,
            token=(sub.get("token") or "").strip() or None,
            apps_dir=Path(apps_dir) if apps_dir else DEFAULT_APPS_DIR,
        )

    _AI_SERVICE_MODES = ("live", "test")
    _AI_SERVICE_NO_AUTO_MODE = "no-auto"
    _AI_SERVICE_VALID_MODES = _AI_SERVICE_MODES + (_AI_SERVICE_NO_AUTO_MODE,)

    @classmethod
    def _parse_ai_service_modes(cls, entry: dict, i: int, path: Path) -> tuple[str, ...]:
        """None (the key absent entirely) means both live and test — the
        default every entry had before `modes` existed at all. An
        explicit empty list is different: it deliberately puts the entry
        in neither cascade, rather than falling back to that default."""
        modes = entry.get("modes")
        if modes is None:
            return cls._AI_SERVICE_MODES
        if not isinstance(modes, list) or not all(isinstance(m, str) for m in modes):
            raise ConfigError(f"{path}: 'ai-service.providers[{i}].modes' must be a list of strings if present.")
        invalid = sorted(set(modes) - set(cls._AI_SERVICE_VALID_MODES))
        if invalid:
            raise ConfigError(
                f"{path}: 'ai-service.providers[{i}].modes' contains invalid entr{'y' if len(invalid) == 1 else 'ies'} "
                f"{invalid} — must be 'live', 'test', and/or 'no-auto'."
            )
        return tuple(dict.fromkeys(modes))

    @classmethod
    def _parse_ai_services(cls, raw: dict, path: Path) -> list[AIServiceConfig]:
        entries = cls._get_providers(raw, "ai-service", path)
        max_output_tokens = cls._get_optional_positive_int(
            raw, "ai-service", "max-output-tokens", path, default=4096
        )

        services = []
        for i, entry in enumerate(entries):
            prefix = cls._provider_prefix(entry, "ai-service", i, path)
            url = entry.get("url")
            driver = cls._require_str_in(entry, prefix, "driver", path)
            model = cls._require_str_in(entry, prefix, "model", path)
            key = entry.get("key")
            if not isinstance(key, str):
                raise ConfigError(f"{path}: '{prefix}.key' must be a string.")
            ui_label, ui_description = cls._parse_ui_fields(entry, driver, "ai-service", i, path)
            modes = cls._parse_ai_service_modes(entry, i, path)
            token_budget_per_day = cls._positive_int_in(entry, prefix, "token-budget-per-day", path, 1_000_000)
            services.append(AIServiceConfig(
                driver=driver, model=model, key=key, url=url,
                ui_label=ui_label, ui_description=ui_description,
                max_output_tokens=max_output_tokens, modes=modes,
                token_budget_per_day=token_budget_per_day,
            ))
        for mode in cls._AI_SERVICE_MODES:
            matching = [service for service in services if mode in service.modes]
            if not matching:
                raise ConfigError(f"{path}: 'ai-service.providers' has no entry left for mode {mode!r}.")
            if all(cls._AI_SERVICE_NO_AUTO_MODE in service.modes for service in matching):
                raise ConfigError(
                    f"{path}: 'ai-service.providers' has no entry left for mode {mode!r} that isn't "
                    f"{cls._AI_SERVICE_NO_AUTO_MODE!r} — its auto cascade would be empty."
                )
        return services

    def __init__(self) -> None:

        raw, path = self._load_yml()
        self.raw = raw
        self.path = path
        if not isinstance(raw, dict):
            raise ConfigError(f"{path} must contain a YAML mapping at the top level.")
        assert path is not None

        self.database_url = self._require_str(raw, "database", "url", path)
        self.database_migration_strategy = self._get_optional_choice(
            raw, "database", "migration-strategy", path, default="stop", choices=("stop", "upgrade", "drop")
        )
        self.max_session_duration_in_minutes = self._get_optional_positive_float(
            raw, "turn-service", "max-session-duration-in-minutes", path, default=60.0
        )
        # FIXME: 16000 mirrored in TrackingService/TrackingProcessor's own
        self.input_token_budget_per_turn = self._get_optional_positive_int(
            raw, "turn-service", "input-token-budget-per-turn", path, default=16000
        )
        # FIXME: 200000 mirrored in TrackingService's own constructor
        self.total_token_budget_per_session = self._get_optional_positive_int(
            raw, "turn-service", "total-token-budget-per-session", path, default=200000
        )
        # FIXME: the default is mirrored in tracking/project_files.py's
        self.project_file_cache_bytes = self._get_optional_positive_int(
            raw, "turn-service", "project-file-cache-bytes", path, default=8 * 1024 * 1024
        )
        self.jobs_shared_max_concurrent = self._get_optional_positive_int(
            raw, "scheduler-service", "shared-max-concurrent", path, default=2
        )
        self.invite_valid_days = self._get_optional_positive_int(
            raw, "project-service", "invite-valid-days", path, default=7
        )
        self.invite_max_shares = self._get_optional_positive_int(
            raw, "project-service", "invite-max-shares", path, default=3
        )
        self.ai_services = self._parse_ai_services(raw, path)
        self.auth_token_ttl_in_hours = self._get_optional_positive_int(
            raw, "auth-service", "token-ttl-in-hours", path, default=24 * 7
        )
        self.auth_providers = self._parse_auth_providers(raw, path)

        self.build_service_config = self._parse_build_service_config(raw, path)

    @staticmethod
    def _public_provider_fields(entry) -> dict:
        return {
            "driver": entry.driver,
            "model": entry.model,
            "ui-label": entry.ui_label,
            "ui-description": entry.ui_description,
        }

    def public_services_snapshot(self) -> dict:
        """Read-only projection of this config's service sections — same
        section/field names as .config.yml itself, so the frontend's
        Settings > Manage services page can show it as-is. Every provider
        key, the database url's own credentials, and jwt-secret are
        stripped out here (the one place secrets are parsed in the first
        place) rather than downstream — nothing else ever gets a chance
        to leak them, except whatsapp's own three secrets below, sent
        as-is (admin-only route) for Manage services' masked/revealable
        fields."""
        snapshot = {
            "chat": ui_section("Chat", "Session limits and token budgets for every conversation.", {
                "max-session-duration-in-minutes": self.max_session_duration_in_minutes,
                "input-token-budget-per-turn": self.input_token_budget_per_turn,
                "total-token-budget-per-session": self.total_token_budget_per_session,
                "project-file-cache-bytes": self.project_file_cache_bytes,
            }),
            "ai": ui_section("AI", "Language model providers and the live cascade between them.", {
                "max-output-tokens": self.ai_services[0].max_output_tokens,
                "providers": [
                    {
                        **self._public_provider_fields(p), "url": p.url, "modes": list(p.modes),
                        "token-budget-per-day": p.token_budget_per_day,
                    }
                    for p in self.ai_services
                ],
            }),
            "database": ui_section("Data", "Database connection, backups and stored sessions.", {
                "url": _redact_database_url(self.database_url),
                "migration-strategy": self.database_migration_strategy,
            }),
            "build": self._public_build_service_fields(),
        }
        return bus.collect(POINT_CONFIG_SERVICES, snapshot)

    def _public_build_service_fields(self) -> dict:
        b = self.build_service_config
        return {
            "repo-url": b.repo_url,
            "username": b.username,
            "token": b.token,
            "apps-dir": str(b.apps_dir),
        }


def parse_ui_fields(entry: dict, driver: str, section: str, i: int, path: Path) -> tuple[str, str | None]:
    return AppConfig._parse_ui_fields(entry, driver, section, i, path)


def optional_providers(raw: dict, section: str, path: Path) -> list | None:
    return AppConfig._get_optional_providers(raw, section, path)


def optional_section(raw: dict, section: str, path: Path) -> dict:
    return AppConfig._get_optional_section(raw, section, path)


def provider_prefix(entry: object, section: str, i: int, path: Path) -> str:
    return AppConfig._provider_prefix(entry, section, i, path)


def require_str(sub: dict, section: str, field: str, path: Path) -> str:
    return AppConfig._require_str_in(sub, section, field, path)


def optional_str(sub: dict, section: str, field: str, path: Path) -> str | None:
    return AppConfig._optional_str_in(sub, section, field, path)


def optional_choice(sub: dict, section: str, field: str, path: Path, default: str, choices: tuple[str, ...]) -> str:
    return AppConfig._choice_in(sub, section, field, path, default, choices)


def optional_positive_int(sub: dict, section: str, field: str, path: Path, default: int) -> int:
    return AppConfig._positive_int_in(sub, section, field, path, default)


def optional_non_negative_int(sub: dict, section: str, field: str, path: Path, default: int) -> int:
    return AppConfig._non_negative_int_in(sub, section, field, path, default)
