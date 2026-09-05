from __future__ import annotations

from tracking.env import Env


class EphemeralEnvRegistry(object):
    _instance: "EphemeralEnvRegistry | None" = None

    def __new__(cls) -> "EphemeralEnvRegistry":
        if cls._instance is None:
            instance = super().__new__(cls)
            instance._envs = {}
            cls._instance = instance
        return cls._instance

    def get(self, session_id: int) -> Env:
        env = self._envs.get(session_id)
        if env is None:
            env = Env()
            self._envs[session_id] = env
        return env

    def discard(self, session_id: int) -> None:
        self._envs.pop(session_id, None)

    @classmethod
    def _reset_for_tests(cls) -> None:
        cls._instance = None
