from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit
from ruamel.yaml import YAML

from system import bus
from system.bus import POINT_CONFIG_SERVICES
from system.config_services import ui_section
DEFAULT_APPS_DIR = Path(__file__).resolve().parent.parent / "apps"

DEFAULT_ALLOWED_ORIGINS = ("http://localhost:5173", "http://127.0.0.1:5173")

REPLY_SILENCE_SECONDS = 45.0



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
class ReplyDeadline:
    """How long a turn may go without a new streamed chunk before the
    browser gives up — parsed here (turn-service is core), handed to
    ai.StreamDeadline(**dataclasses.asdict(...)) by the ai skill, whose
    own field names and defaults this deliberately mirrors, since this
    module must stay importable in a build without the ai skill at all."""
    first_chunk_seconds: float = 10.0
    next_chunk_seconds: float = 10.0
    silent_round_seconds: float = 30.0
    first_thought_seconds: float = 3.0


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
    def _parse_stream_deadline(cls, raw: dict, path: Path) -> ReplyDeadline:
        default = ReplyDeadline()
        return ReplyDeadline(
            first_chunk_seconds=cls._get_optional_positive_float(
                raw, "turn-service", "first-chunk-seconds", path, default.first_chunk_seconds,
            ),
            next_chunk_seconds=cls._get_optional_positive_float(
                raw, "turn-service", "next-chunk-seconds", path, default.next_chunk_seconds,
            ),
            silent_round_seconds=cls._get_optional_positive_float(
                raw, "turn-service", "silent-round-seconds", path, default.silent_round_seconds,
            ),
            first_thought_seconds=cls._get_optional_positive_float(
                raw, "turn-service", "first-thought-seconds", path, default.first_thought_seconds,
            ),
        )

    @classmethod
    def _parse_reply_silence_seconds(cls, raw: dict, path: Path, deadline: ReplyDeadline) -> float:
        seconds = cls._get_optional_positive_float(
            raw, "turn-service", "reply-silence-seconds", path, REPLY_SILENCE_SECONDS,
        )
        if seconds <= deadline.silent_round_seconds:
            raise ConfigError(
                f"{path}: 'turn-service.reply-silence-seconds' ({seconds:g}) must exceed "
                f"'turn-service.silent-round-seconds' ({deadline.silent_round_seconds:g}): the browser would give up "
                f"on a reply the server is still allowed to be writing."
            )
        return seconds

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
    def _parse_allowed_origins(cls, raw: dict, path: Path) -> list[str]:
        sub = cls._get_optional_section(raw, "web", path)
        origins = sub.get("allowed-origins")
        if origins is None:
            return list(DEFAULT_ALLOWED_ORIGINS)
        if not isinstance(origins, list) or not all(isinstance(o, str) and o.strip() for o in origins):
            raise ConfigError(f"{path}: 'web.allowed-origins' must be a list of non-empty strings if present.")
        return [o.strip().rstrip("/") for o in origins]

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
        self.stream_deadline = self._parse_stream_deadline(raw, path)
        self.reply_silence_seconds = self._parse_reply_silence_seconds(raw, path, self.stream_deadline)
        self.jobs_shared_max_concurrent = self._get_optional_positive_int(
            raw, "scheduler-service", "shared-max-concurrent", path, default=2
        )
        self.invite_valid_days = self._get_optional_positive_int(
            raw, "project-service", "invite-valid-days", path, default=7
        )
        self.invite_max_shares = self._get_optional_positive_int(
            raw, "project-service", "invite-max-shares", path, default=3
        )
        self.auth_token_ttl_in_hours = self._get_optional_positive_int(
            raw, "auth-service", "token-ttl-in-hours", path, default=24 * 7
        )
        self.auth_providers = self._parse_auth_providers(raw, path)

        self.build_service_config = self._parse_build_service_config(raw, path)
        self.allowed_origins = self._parse_allowed_origins(raw, path)

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
            "chat": ui_section("Chat", "Session limits, token budgets and reply deadlines for every conversation.", {
                "max-session-duration-in-minutes": self.max_session_duration_in_minutes,
                "input-token-budget-per-turn": self.input_token_budget_per_turn,
                "total-token-budget-per-session": self.total_token_budget_per_session,
                "project-file-cache-bytes": self.project_file_cache_bytes,
                "first-thought-seconds": self.stream_deadline.first_thought_seconds,
                "first-chunk-seconds": self.stream_deadline.first_chunk_seconds,
                "next-chunk-seconds": self.stream_deadline.next_chunk_seconds,
                "silent-round-seconds": self.stream_deadline.silent_round_seconds,
                "reply-silence-seconds": self.reply_silence_seconds,
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


def providers(raw: dict, section: str, path: Path) -> list:
    return AppConfig._get_providers(raw, section, path)


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
