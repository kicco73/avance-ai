from __future__ import annotations

from system import bus

REQUIRED = "required"
OPTIONAL = "optional"
DISABLED = "disabled"


class OptionalService:

    name = OPTIONAL

    def narrow(self, installed: bool) -> bool:
        return installed

    async def deliver(self, message: bus.Message, sender: bus.Sender) -> None:
        await bus.publish_with_bounceback(message, sender)

    async def publish(self, message: bus.Message) -> bool:
        return await bus.publish(message)


class RequiredService(OptionalService):

    name = REQUIRED


class DisabledService(OptionalService):

    name = DISABLED

    def narrow(self, installed: bool) -> bool:
        return False

    async def deliver(self, message: bus.Message, sender: bus.Sender) -> None:
        await sender.bounced(message)

    async def publish(self, message: bus.Message) -> bool:
        return False


_LEVELS = {REQUIRED: RequiredService, OPTIONAL: OptionalService, DISABLED: DisabledService}
_ABSENT = OptionalService()


class ProjectServices:

    def __init__(self, declared: dict[str, str] | None = None) -> None:
        self._declared = dict(declared or {})
        self._levels = {key: _LEVELS[level]() for key, level in self._declared.items()}

    def __getitem__(self, key: str) -> OptionalService:
        return self._levels.get(key, _ABSENT)

    def __eq__(self, other) -> bool:
        return isinstance(other, ProjectServices) and other._declared == self._declared

    def __repr__(self) -> str:
        return f"ProjectServices({self._declared!r})"

    def required_keys(self) -> set[str]:
        return {key for key, level in self._declared.items() if level == REQUIRED}

    def disabled_keys(self) -> set[str]:
        return {key for key, level in self._declared.items() if level == DISABLED}

    def as_raw(self) -> dict[str, str]:
        return dict(self._declared)


def parse(raw_services) -> ProjectServices:
    if raw_services is None:
        declared = {}
    elif isinstance(raw_services, dict):
        declared = dict(raw_services)
    else:
        raise ValueError(
            f"project.services must be a mapping of service name to one of {sorted(_LEVELS)}, "
            f"got {type(raw_services).__name__}."
        )
    for key, level in declared.items():
        if level not in _LEVELS:
            raise ValueError(f"project.services.{key} {level!r} must be one of {sorted(_LEVELS)}.")
    return ProjectServices(declared)
