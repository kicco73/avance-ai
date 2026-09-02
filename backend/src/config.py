from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit
from ruamel.yaml import YAML

from ai.llm_provider import AIServiceConfig

_yaml = YAML(typ='rt')


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
class NotificationServiceConfig:
    url: str
    username: str
    password: str
    from_name: str | None
    timeout_seconds: int


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
class WhatsAppServiceConfig:
    """The optional `whatsapp-service` section (see docs/WHATSAPP.md):
    Meta Cloud API credentials plus the phone -> account mapping."""
    verify_token: str
    app_secret: str
    access_token: str
    phone_number_id: str
    phone_number: str | None
    graph_version: str
    mark_read: bool
    # When the bot answers with a voice note instead of text (needs
    # talk-service): "never", "when-spoken-to" (only in reply to a voice
    # note — the default), "always" (every reply that has an [audio] text).
    voice_replies: str


@dataclass(frozen=True)
class AuthProviderConfig:
    driver: str
    # Mandatory here — Google always requires a client ID; revisit whether
    # this should become optional if a future provider doesn't need one.
    key: str
    # Optional: falls back to `driver` (see AppConfig._parse_auth_providers).
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
    def _get_optional_non_negative_int(
        cls, raw: dict, section: str, field: str, path: Path, default: int
    ) -> int:
        sub = cls._get_optional_section(raw, section, path)
        value = sub.get(field, default)
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ConfigError(f"{path}: '{section}.{field}' must be a non-negative integer if present.")
        return value

    @classmethod
    def _get_optional_choice(cls, raw: dict, section: str, field: str, path: Path, default: str, choices: tuple[str, ...]) -> str:
        sub = cls._get_section(raw, section, path)
        value = sub.get(field, default)
        if value not in choices:
            raise ConfigError(f"{path}: '{section}.{field}' must be one of {', '.join(choices)} if present.")
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
    def _parse_auth_providers(cls, raw: dict, path: Path) -> list[AuthProviderConfig]:
        # Always required (unlike talk-service/listen-service, which are
        # opt-in via _get_optional_providers): authentication isn't an
        # optional feature once the login wall exists.
        entries = cls._get_providers(raw, "auth-service", path)

        providers = []
        for i, entry in enumerate(entries):
            if not isinstance(entry, dict):
                raise ConfigError(f"{path}: 'auth-service.providers[{i}]' must be a mapping.")
            driver = entry.get("driver")
            key = entry.get("key")
            if not isinstance(driver, str) or not driver.strip():
                raise ConfigError(f"{path}: 'auth-service.providers[{i}].driver' is missing or empty.")
            if not isinstance(key, str) or not key.strip():
                raise ConfigError(f"{path}: 'auth-service.providers[{i}].key' is missing or empty.")
            driver = driver.strip()
            ui_label, ui_description = cls._parse_ui_fields(entry, driver, "auth-service", i, path)
            providers.append(AuthProviderConfig(
                driver=driver, key=key.strip(), ui_label=ui_label, ui_description=ui_description,
            ))
        return providers

    _WHATSAPP_VOICE_REPLIES = ("never", "when-spoken-to", "always")

    @classmethod
    def _parse_whatsapp_service_config(cls, raw: dict, path: Path) -> WhatsAppServiceConfig | None:
        """Same optional, default-off shape as talk-service.enabled: no
        section (or enabled: false) means no WhatsApp channel is built
        and its webhook routes are never registered."""
        sub = cls._get_optional_section(raw, "whatsapp-service", path)
        if not sub.get("enabled", False):
            return None
        section = "whatsapp-service"
        verify_token = cls._require_str(raw, section, "verify-token", path)
        app_secret = cls._require_str(raw, section, "app-secret", path)
        access_token = cls._require_str(raw, section, "access-token", path)
        # YAML reads an unquoted 1223547060851510 as an int: accept both
        # (going through _require_str first would reject the int).
        phone_number_id = sub.get("phone-number-id")
        if isinstance(phone_number_id, int) and not isinstance(phone_number_id, bool):
            phone_number_id = str(phone_number_id)
        if not isinstance(phone_number_id, str) or not phone_number_id.strip():
            raise ConfigError(f"{path}: '{section}.phone-number-id' is missing or empty.")
        phone_number_id = phone_number_id.strip()

        phone_number = sub.get("phone-number")
        if phone_number is not None:
            if not isinstance(phone_number, str):
                raise ConfigError(f"{path}: '{section}.phone-number' must be a string if present.")
            phone_number = phone_number.strip().lstrip("+")
            if not phone_number.isdigit():
                raise ConfigError(f"{path}: '{section}.phone-number' must be digits only (E.164, no '+').")

        graph_version = sub.get("graph-version", "v23.0")
        if not isinstance(graph_version, str) or not graph_version.strip():
            raise ConfigError(f"{path}: '{section}.graph-version' must be a non-empty string if present.")
        mark_read = sub.get("mark-read", True)
        if not isinstance(mark_read, bool):
            raise ConfigError(f"{path}: '{section}.mark-read' must be a boolean if present.")
        voice_replies = cls._get_optional_choice(
            raw, section, "voice-replies", path, default="when-spoken-to", choices=cls._WHATSAPP_VOICE_REPLIES,
        )
        return WhatsAppServiceConfig(
            verify_token=verify_token, app_secret=app_secret, access_token=access_token,
            phone_number_id=phone_number_id, phone_number=phone_number,
            graph_version=graph_version.strip(), mark_read=mark_read, voice_replies=voice_replies,
        )

    @classmethod
    def _parse_notification_service_config(cls, raw: dict, path: Path) -> NotificationServiceConfig | None:
        """None if the whole section is absent — actuator.send_mail (see
        tracking/actuators/actuator_set.py) is the only thing that needs
        a NotificationService; nothing else in the system requires one.
        A section that IS present still gets every field required below,
        same as before — a deliberately-added-but-broken section is
        still a real misconfiguration, not silently skipped."""
        if raw.get("notification-service") is None:
            return None
        url = cls._require_str(raw, "notification-service", "url", path)
        username = cls._require_str(raw, "notification-service", "username", path)
        password = cls._require_str(raw, "notification-service", "password", path)
        sub = cls._get_section(raw, "notification-service", path)
        from_name = sub.get("from-name")
        if from_name is not None and not isinstance(from_name, str):
            raise ConfigError(f"{path}: 'notification-service.from-name' must be a string if present.")
        timeout_seconds = cls._get_optional_positive_int(
            raw, "notification-service", "timeout-seconds", path, default=10
        )
        return NotificationServiceConfig(
            url=url, username=username, password=password,
            from_name=from_name.strip() if from_name and from_name.strip() else None,
            timeout_seconds=timeout_seconds,
        )

    _AI_SERVICE_MODES = ("live", "test")

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
        invalid = sorted(set(modes) - set(cls._AI_SERVICE_MODES))
        if invalid:
            raise ConfigError(
                f"{path}: 'ai-service.providers[{i}].modes' contains invalid entr{'y' if len(invalid) == 1 else 'ies'} "
                f"{invalid} — must be 'live' and/or 'test'."
            )
        return tuple(dict.fromkeys(modes))  # de-duplicated, order preserved

    @classmethod
    def _parse_ai_services(cls, raw: dict, path: Path) -> list[AIServiceConfig]:
        entries = cls._get_providers(raw, "ai-service", path)
        # One cap for every provider in the cascade — the ceiling a single
        # generate_stream_with_schema call is allowed to reach before the
        # provider itself reports truncation (see AIServiceProviderOutputTruncatedError).
        max_output_tokens = cls._get_optional_positive_int(
            raw, "ai-service", "max-output-tokens", path, default=4096
        )

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
            modes = cls._parse_ai_service_modes(entry, i, path)
            services.append(AIServiceConfig(
                driver=driver, model=model.strip(), key=key, url=url,
                ui_label=ui_label, ui_description=ui_description,
                max_output_tokens=max_output_tokens, modes=modes,
            ))
        # AiService.for_live/for_test each filter this same list down to
        # only the entries whose own modes include that one (see
        # AIServiceConfig.modes) — every entry opting out of a mode (or
        # every entry sharing the same non-default modes) could otherwise
        # leave one of the two cascades silently empty, a startup-time
        # misconfiguration worth catching here rather than as a confusing
        # runtime failure the first time that mode is actually used.
        for mode in cls._AI_SERVICE_MODES:
            if not any(mode in service.modes for service in services):
                raise ConfigError(f"{path}: 'ai-service.providers' has no entry left for mode {mode!r}.")
        return services

    def __init__(self) -> None:

        raw, path = self._load_yml()
        if not isinstance(raw, dict):
            raise ConfigError(f"{path} must contain a YAML mapping at the top level.")

        self.database_url = self._require_str(raw, "database", "url", path)
        self.database_migration_strategy = self._get_optional_choice(
            raw, "database", "migration-strategy", path, default="stop", choices=("stop", "upgrade", "drop")
        )
        # The single source of truth for how long a chat session stays
        # "open" (see chat/session_manager.py's ChatSessionManager) — never
        # hardcoded elsewhere.
        self.max_session_duration_in_minutes = self._get_optional_positive_float(
            raw, "chat-service", "max-session-duration-in-minutes", path, default=60.0
        )
        # FIXME: 16000 mirrored in TrackingService/TrackingProcessor's own
        # constructor defaults — keep in sync.
        self.input_token_budget_per_turn = self._get_optional_positive_int(
            raw, "chat-service", "input-token-budget-per-turn", path, default=16000
        )
        # FIXME: 200000 mirrored in TrackingService's own constructor
        # default — keep in sync. Display-only (see SessionDetailCard.vue's
        # tokens bar): the max reference the bar is drawn against, nothing
        # in the backend trims history against it.
        self.total_token_budget_per_session = self._get_optional_positive_int(
            raw, "chat-service", "total-token-budget-per-session", path, default=200000
        )

        # Two separate worker pools (see jobs/job_queue.py's JobQueue and
        # jobs/throttled_job_queue.py's ThrottledJobQueue) — optional, and
        # so is the whole `jobs` section.
        self.jobs_shared_max_concurrent = self._get_optional_positive_int(
            raw, "jobs", "shared-max-concurrent", path, default=2
        )
        self.test_service_max_concurrent_tests = self._get_optional_positive_int(
            raw, "test-service", "max-concurrent-tests", path, default=4
        )
        self.test_service_max_tests_per_minute = self._get_optional_positive_int(
            raw, "test-service", "max-tests-per-minute", path, default=1_000_000
        )
        self.test_service_min_test_interval_ms = self._get_optional_non_negative_int(
            raw, "test-service", "min-test-interval-ms", path, default=0
        )

        # "Share project" invite links (see project/invites.py's own
        # InviteManager) — how long a freshly generated code stays
        # redeemable, and how many new registrations it can carry before
        # AuthService.complete_registration starts refusing it.
        self.invite_valid_days = self._get_optional_positive_int(
            raw, "project-service", "invite-valid-days", path, default=7
        )
        self.invite_max_shares = self._get_optional_positive_int(
            raw, "project-service", "invite-max-shares", path, default=3
        )

        self.ai_services = self._parse_ai_services(raw, path)
        self.talk_services = self._parse_talk_services(raw, path)
        self.listen_services = self._parse_listen_services(raw, path)

        # Not provider-specific — needed regardless of which auth provider
        # actually authenticated the user.
        self.auth_token_ttl_in_hours = self._get_optional_positive_int(
            raw, "auth-service", "token-ttl-in-hours", path, default=24 * 7
        )
        self.auth_providers = self._parse_auth_providers(raw, path)

        self.notification_service_config = self._parse_notification_service_config(raw, path)

        self.whatsapp_service_config = self._parse_whatsapp_service_config(raw, path)

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
        wa = self.whatsapp_service_config
        return {
            "chat": {
                "max-session-duration-in-minutes": self.max_session_duration_in_minutes,
                "input-token-budget-per-turn": self.input_token_budget_per_turn,
                "total-token-budget-per-session": self.total_token_budget_per_session,
            },
            "testing": {
                "max-concurrent-tests": self.test_service_max_concurrent_tests,
                "max-tests-per-minute": self.test_service_max_tests_per_minute,
                "min-test-interval-ms": self.test_service_min_test_interval_ms,
            },
            "ai": {
                "max-output-tokens": self.ai_services[0].max_output_tokens,
                "providers": [
                    {**self._public_provider_fields(p), "url": p.url, "modes": list(p.modes)}
                    for p in self.ai_services
                ],
            },
            "talk": {
                "enabled": self.talk_services is not None,
                "providers": [self._public_provider_fields(p) for p in (self.talk_services or [])],
            },
            "listen": {
                "enabled": self.listen_services is not None,
                "providers": [
                    {**self._public_provider_fields(p), "language": p.language}
                    for p in (self.listen_services or [])
                ],
            },
            "whatsapp": {
                "enabled": wa is not None,
                "verify-token": wa.verify_token if wa else None,
                "app-secret": wa.app_secret if wa else None,
                "access-token": wa.access_token if wa else None,
                "phone-number-id": wa.phone_number_id if wa else None,
                "phone-number": wa.phone_number if wa else None,
                "graph-version": wa.graph_version if wa else None,
                "mark-read": wa.mark_read if wa else None,
            },
            "database": {
                "url": _redact_database_url(self.database_url),
                "migration-strategy": self.database_migration_strategy,
            },
        }
