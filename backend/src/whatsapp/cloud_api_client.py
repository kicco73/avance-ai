"""Thin async client for the Meta WhatsApp Cloud API (the outbound half of
the channel — see docs/WHATSAPP.md). Only what the channel needs: send
a text, buttons or an audio, mark a message read, move media in and out. Never touches Avance's own services."""
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
        return self._checked(response)

    @staticmethod
    def _checked(response: httpx.Response) -> dict:
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

    async def send_audio(self, to: str, media_id: str) -> dict:
        """An already-uploaded audio (see upload_media). OGG/Opus renders
        as a voice note; other types (MP3 here) as a plain audio message."""
        return await self._post({
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": to,
            "type": "audio",
            "audio": {"id": media_id},
        })

    async def upload_media(self, data: bytes, mime_type: str, filename: str = "audio.mp3") -> str:
        """Uploads `data` to the business number's media store and returns
        Meta's media id, valid for 30 days — the handle send_audio takes."""
        response = await self._client.post(
            f"/{self._phone_number_id}/media",
            data={"messaging_product": "whatsapp", "type": mime_type},
            files={"file": (filename, data, mime_type)},
        )
        return self._checked(response)["id"]

    async def download_media(self, media_id: str) -> tuple[bytes, str]:
        """(bytes, mime_type) for an inbound media id. Two hops: the media
        node gives a short-lived (5 min) download URL, which itself must be
        fetched with the same Bearer token or Meta answers 404."""
        meta = self._checked(await self._client.get(f"/{media_id}"))
        response = await self._client.get(meta["url"])
        if response.status_code >= 400:
            logger.error(f"WhatsApp media download {response.status_code} for {media_id}")
            response.raise_for_status()
        return response.content, meta.get("mime_type", "")

    async def mark_read(self, message_id: str) -> None:
        """Best effort: the blue tick is cosmetic, a failure is logged and swallowed."""
        try:
            await self._post({"messaging_product": "whatsapp", "status": "read", "message_id": message_id})
        except httpx.HTTPError as exc:
            logger.warning(f"mark_read failed for {message_id}: {exc}")

    async def mark_read_and_show_typing(self, message_id: str) -> None:
        """Same read receipt, plus the "typing..." indicator the Cloud API
        only accepts inside that very request: it stays on for at most 25
        seconds and is dismissed by our reply. Best effort, like mark_read."""
        try:
            await self._post({
                "messaging_product": "whatsapp",
                "status": "read",
                "message_id": message_id,
                "typing_indicator": {"type": "text"},
            })
        except httpx.HTTPError as exc:
            logger.warning(f"mark_read_and_show_typing failed for {message_id}: {exc}")

    async def send_buttons(self, to: str, body: str, buttons: list[tuple[str, str]]) -> dict:
        return await self._post({
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": to,
            "type": "interactive",
            "interactive": {
                "type": "button",
                "body": {"text": body},
                "action": {
                    "buttons": [
                        {"type": "reply", "reply": {"id": button_id, "title": title}}
                        for button_id, title in buttons
                    ],
                },
            },
        })

    async def send_list(self, to: str, body: str, button_text: str, rows: list[tuple[str, str, str | None]]) -> dict:
        return await self._post({
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": to,
            "type": "interactive",
            "interactive": {
                "type": "list",
                "body": {"text": body},
                "action": {
                    "button": button_text,
                    "sections": [{"rows": [_list_row(*row) for row in rows]}],
                },
            },
        })


def _list_row(row_id: str, title: str, description: str | None) -> dict:
    row = {"id": row_id, "title": title}
    if description:
        row["description"] = description
    return row


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
