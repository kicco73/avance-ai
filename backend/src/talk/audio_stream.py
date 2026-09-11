from __future__ import annotations

from typing import AsyncIterator


class AudioStream(object):

    def __init__(self, talk_service, text: str) -> None:
        self._talk_service = talk_service
        self._text = text

    @property
    def text(self) -> str:
        return self._text

    def chunks(self) -> AsyncIterator[bytes]:
        return self._talk_service.generate(self._text)
