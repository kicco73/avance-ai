"""Meta's webhook surface for the WhatsApp channel (see docs/WHATSAPP.md)
— two public routes (role=None: Meta has no session cookie; the POST is
authenticated by its HMAC signature instead), under /api/ so nginx.conf's
existing /api/ proxy covers them with no config change.

Only registered when `whatsapp-service.enabled` is true (see main.py).
"""
from __future__ import annotations

from http import HTTPStatus

from fastapi import BackgroundTasks, HTTPException, Query, Request, Response

from whatsapp.whatsapp_service import WhatsAppService

from .base_controller import BaseController, get, post


class WhatsAppController(BaseController):

    def __init__(self, whatsapp_service: WhatsAppService) -> None:
        self.whatsapp_service = whatsapp_service

    @get("/api/whatsapp/webhook", role=None)
    def get_webhook_verification(
        self,
        hub_mode: str = Query(alias="hub.mode"),
        hub_verify_token: str = Query(alias="hub.verify_token"),
        hub_challenge: str = Query(alias="hub.challenge"),
    ):
        """Meta's one-time subscription handshake: echo hub.challenge as
        plain text iff the verify token matches ours."""
        if hub_mode == "subscribe" and self.whatsapp_service.is_valid_verify_token(hub_verify_token):
            return Response(content=hub_challenge, media_type="text/plain")
        raise HTTPException(status_code=HTTPStatus.FORBIDDEN, detail="Verify token mismatch.")

    @post("/api/whatsapp/webhook", role=None)
    async def post_webhook(self, request: Request, background: BackgroundTasks):
        """Answers 200 right away and does the actual turn in the
        background: Meta retries (and eventually disables) a webhook that
        answers slowly, and a chat turn takes seconds."""
        raw = await request.body()
        if not self.whatsapp_service.is_valid_signature(raw, request.headers.get("X-Hub-Signature-256")):
            raise HTTPException(status_code=HTTPStatus.FORBIDDEN, detail="Bad signature.")
        payload = await request.json()
        for message in self.whatsapp_service.extract_incoming(payload):
            if self.whatsapp_service.accept(message):
                background.add_task(self.whatsapp_service.handle, message)
        return {"status": "ok"}
