"""The uniform {error: {message, detail}} contract every endpoint's
failures share — registered once, in main.py, via register_error_handlers().
"""
from __future__ import annotations

import logging

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse

from chat_service import ChatServiceError
from ai.llm_provider import LLMProviderError

logger = logging.getLogger(__name__)


def _error_body(message: str, detail: str | None = None) -> dict:
    return {"error": {"message": message, "detail": detail}}


async def http_exception_handler(request: Request, exc: HTTPException) -> JSONResponse:
    # Most routes raise HTTPException(detail=str(...)) — a single readable
    # string with no separate technical detail. ChatServiceError (see POST
    # /api/messages) has both, so its route packs them into a dict detail
    # instead, recognized here rather than string-only everywhere.
    if isinstance(exc.detail, dict) and "message" in exc.detail:
        return JSONResponse(
            status_code=exc.status_code, content=_error_body(exc.detail["message"], exc.detail.get("detail"))
        )
    return JSONResponse(status_code=exc.status_code, content=_error_body(str(exc.detail)))


async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    logger.exception("Unhandled error on %s %s", request.method, request.url.path)
    return JSONResponse(status_code=500, content=_error_body("Internal server error.", str(exc)))


async def ai_service_error_handler(request: Request, exc: LLMProviderError) -> JSONResponse:
    logger.exception("LLMProvider error on %s %s", request.method, request.url.path)
    return JSONResponse(status_code=exc.status_code, content=_error_body(exc.message, exc.detail))


async def chat_service_error_handler(request: Request, exc: ChatServiceError) -> JSONResponse:
    logger.exception("ChatService error on %s %s", request.method, request.url.path)
    return JSONResponse(status_code=exc.status_code, content=_error_body(exc.message, exc.detail))


def register_error_handlers(app: FastAPI) -> None:
    app.add_exception_handler(Exception, unhandled_exception_handler)
    app.add_exception_handler(HTTPException, http_exception_handler)
    app.add_exception_handler(LLMProviderError, ai_service_error_handler)
    app.add_exception_handler(ChatServiceError, chat_service_error_handler)
