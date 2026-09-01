"""Thin async client for the Meta WhatsApp Cloud API (the outbound half of
the channel — see docs/WHATSAPP.md). Only what the channel needs: send
a text, mark a message read. Never touches Avance's own services."""
from __future__ import annotations

import httpx

from logging_factory import LoggerFactory

logger = LoggerFactory.get_logger(__name__)

# WhatsApp's own hard limit for one text message body.
WA_TEXT_LIMIT = 4096


class WhatsAppCloudApiClient(object):
    def __init__(self, access_token: str, phone_number_id: str, graph_version: str, timeout: float = 15.0) -> None:
        self._phone_number_id = phone_number_id
        self._client = httpx.AsyncClient(
            base_url=f"https://graph.facebook.com/{graph_version}",
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=timeout,
        )

    async def close(self) -> None:
        await self._client.aclose()

    async def _post(self, payload: dict) -> dict:
        response = await self._client.post(f"/{self._phone_number_id}/messages", json=payload)
        if response.status_code >= 400:
            logger.error(f"WhatsApp Cloud API {response.status_code}: {response.text}")
            response.raise_for_status()
        return response.json()

    async def send_text(self, to: str, body: str) -> list[dict]:
        """One Cloud API call per chunk — a body over WA_TEXT_LIMIT is
        split on a newline/space boundary rather than rejected."""
        results = []
        for chunk in split_text(body, WA_TEXT_LIMIT):
            results.append(await self._post({
                "messaging_product": "whatsapp",
                "recipient_type": "individual",
                "to": to,
                "type": "text",
                "text": {"preview_url": False, "body": chunk},
            }))
        return results

    async def mark_read(self, message_id: str) -> None:
        """Best effort: the blue tick is cosmetic, a failure is logged and swallowed."""
        try:
            await self._post({"messaging_product": "whatsapp", "status": "read", "message_id": message_id})
        except httpx.HTTPError as exc:
            logger.warning(f"mark_read failed for {message_id}: {exc}")


def split_text(text: str, limit: int) -> list[str]:
    if len(text) <= limit:
        return [text]
    chunks, rest = [], text
    while len(rest) > limit:
        cut = rest.rfind("\n", 0, limit)
        if cut < limit // 2:
            cut = rest.rfind(" ", 0, limit)
        if cut < limit // 2:
            cut = limit
        chunks.append(rest[:cut].rstrip())
        rest = rest[cut:].lstrip()
    if rest:
        chunks.append(rest)
    return chunks
