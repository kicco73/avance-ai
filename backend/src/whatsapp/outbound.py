from __future__ import annotations

from system.logging_factory import LoggerFactory
from whatsapp.cloud_api_client import WhatsAppCloudApiClient
from whatsapp.notices import OPTIONS_PROMPT
from whatsapp.replies import Reply, TextReply, VoiceReply

logger = LoggerFactory.get_logger(__name__)

_MAX_REPLY_BUTTONS = 3
_MAX_LIST_ROWS = 10
_BUTTON_TITLE_LIMIT = 20
_LIST_ROW_TITLE_LIMIT = 24
_LIST_ROW_DESCRIPTION_LIMIT = 72
_INTERACTIVE_BODY_LIMIT = 1024
_LIST_BUTTON_TEXT = "Options"


def _truncate(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[:limit - 1].rstrip() + "…"


class Outbound(object):

    def __init__(self, client: WhatsAppCloudApiClient) -> None:
        self._client = client

    async def say(self, to: str, reply: Reply, delivery: TextReply | VoiceReply) -> None:
        for _ in filter(None, [reply.text]):
            await delivery.send(self._client, to, reply)

    async def answer(
        self, to: str, reply: Reply, choices: list[dict] | None, delivery: TextReply | VoiceReply,
    ) -> None:
        for actions in filter(None, [choices]):
            await self.offer(to, await self._body_for(to, reply, delivery), actions)
            return
        await self.say(to, reply, delivery)

    async def offer(self, to: str, body: str, actions: list[dict]) -> None:
        body = await self._short_enough(to, body)
        actions = self._within_limits(actions)
        for _ in filter(None, [len(actions) <= _MAX_REPLY_BUTTONS]):
            logger.info(f"WhatsApp: sending buttons to {to}: {[a['name'] for a in actions]}.")
            await self._client.send_buttons(to, body, [
                (a["name"], _truncate(a["ui_button"], _BUTTON_TITLE_LIMIT)) for a in actions
            ])
            return
        logger.info(f"WhatsApp: sending a list to {to}: {[a['name'] for a in actions]}.")
        await self._client.send_list(to, body, _LIST_BUTTON_TEXT, [
            (
                a["name"],
                _truncate(a["ui_button"], _LIST_ROW_TITLE_LIMIT),
                _truncate(a["ui_description"], _LIST_ROW_DESCRIPTION_LIMIT) if a["ui_description"] else None,
            )
            for a in actions
        ])

    async def _body_for(self, to: str, reply: Reply, delivery: TextReply | VoiceReply) -> str:
        for _ in filter(None, [not reply.text]):
            return OPTIONS_PROMPT
        for _ in filter(None, [await delivery.spoke(self._client, to, reply)]):
            return OPTIONS_PROMPT
        return reply.text

    async def _short_enough(self, to: str, body: str) -> str:
        for _ in filter(None, [len(body) > _INTERACTIVE_BODY_LIMIT]):
            await self._client.send_text(to, body)
            return OPTIONS_PROMPT
        return body

    @staticmethod
    def _within_limits(actions: list[dict]) -> list[dict]:
        for _ in filter(None, [len(actions) > _MAX_LIST_ROWS]):
            logger.warning(
                f"WhatsApp: {len(actions)} manual actions offered, sending only the first {_MAX_LIST_ROWS}."
            )
            return actions[:_MAX_LIST_ROWS]
        return actions
