"""Meta's webhook surface for the WhatsApp channel (see docs/WHATSAPP.md)
— two public routes (role=None: Meta has no session cookie; the POST is
authenticated by its HMAC signature instead), under /api/ so nginx.conf's
existing /api/ proxy covers them with no config change.

Lives in this package rather than under controllers/ for the same reason
listen's does: a route written into a shared file is registered by the
core whether or not the channel is in this build. Here nothing registers
it unless this package is present (see whatsapp/skill.py).
"""
from __future__ import annotations

from http import HTTPStatus

from fastapi import BackgroundTasks, HTTPException, Query, Request, Response

from controllers.base_controller import BaseController, get, post
from whatsapp.config import WhatsAppServiceConfig
from whatsapp.webhook import SeenMessages, extract_incoming, is_valid_signature, is_valid_verify_token
from whatsapp.whatsapp_service import WhatsAppService


class WhatsAppController(BaseController):
    """Meta's surface, and nothing past it: the handshake, the signature,
    the envelope, and the redeliveries Meta sends when this did not
    answer 200 fast enough. What it hands on is one message a person
    sent; what happens to it is the service's business."""

    def __init__(self, whatsapp_service: WhatsAppService, whatsapp_config: WhatsAppServiceConfig) -> None:
        self.whatsapp_service = whatsapp_service
        self._config = whatsapp_config
        self._seen = SeenMessages()

    @get("/api/skills/whatsapp/webhook", role=None)
    def get_webhook_verification(
        self,
        hub_mode: str = Query(alias="hub.mode"),
        hub_verify_token: str = Query(alias="hub.verify_token"),
        hub_challenge: str = Query(alias="hub.challenge"),
    ):
        """Meta's one-time subscription handshake: echo hub.challenge as
        plain text iff the verify token matches ours."""
        if hub_mode == "subscribe" and is_valid_verify_token(hub_verify_token, self._config.verify_token):
            return Response(content=hub_challenge, media_type="text/plain")
        raise HTTPException(status_code=HTTPStatus.FORBIDDEN, detail="Verify token mismatch.")

    @post("/api/skills/whatsapp/webhook", role=None)
    async def post_webhook(self, request: Request, background: BackgroundTasks):
        """Answers 200 right away and does the actual turn in the
        background: Meta retries (and eventually disables) a webhook that
        answers slowly, and a chat turn takes seconds."""
        raw = await request.body()
        if not is_valid_signature(raw, request.headers.get("X-Hub-Signature-256"), self._config.app_secret):
            raise HTTPException(status_code=HTTPStatus.FORBIDDEN, detail="Bad signature.")
        payload = await request.json()
        for message in extract_incoming(payload):
            if self._seen.check_and_add(message.id):
                background.add_task(self.whatsapp_service.handle, message)
        return {"status": "ok"}
