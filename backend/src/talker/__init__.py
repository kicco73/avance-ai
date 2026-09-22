"""BaseTalker and its human implementation — the seam between the turn
machinery and whoever is actually answering. AiTalker is the ai skill's
own implementation (ai.ai_talker) and is never imported here: core
talker/ must stay importable without ai/ present. Consumers import from
here, never a submodule (mirrors ai/'s own boundary)."""
from .base_talker import BaseTalker, TalkServiceNotAvailableError
from .human_talker import HumanRelay, HumanTalker, HumanTalkerNoRecordingError

__all__ = [
    "BaseTalker", "HumanRelay", "HumanTalker", "HumanTalkerNoRecordingError",
    "TalkServiceNotAvailableError",
]
