from __future__ import annotations

from http import HTTPStatus

from fastapi import BackgroundTasks, HTTPException, Query, Request, Response

from controllers.base_controller import BaseController, get, post
from whatsapp.config import WhatsAppServiceConfig
from whatsapp.webhook import SeenMessages, extract_incoming, is_valid_signature, is_valid_verify_token


class WhatsAppController(BaseController):

    def __init__(self, whatsapp_service, whatsapp_config: WhatsAppServiceConfig) -> None:
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
        if hub_mode == "subscribe" and is_valid_verify_token(hub_verify_token, self._config.verify_token):
            return Response(content=hub_challenge, media_type="text/plain")
        raise HTTPException(status_code=HTTPStatus.FORBIDDEN, detail="Verify token mismatch.")

    @post("/api/skills/whatsapp/webhook", role=None)
    async def post_webhook(self, request: Request, background: BackgroundTasks):
        raw = await request.body()
        if not is_valid_signature(raw, request.headers.get("X-Hub-Signature-256"), self._config.app_secret):
            raise HTTPException(status_code=HTTPStatus.FORBIDDEN, detail="Bad signature.")
        payload = await request.json()
        for message in extract_incoming(payload):
            if self._seen.check_and_add(message.id):
                background.add_task(self.whatsapp_service.receive, message)
        return {"status": "ok"}
