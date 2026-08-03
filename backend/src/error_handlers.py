"""The uniform {error: {message, detail}} contract every endpoint's
failures share — registered once, in main.py, via register_error_handlers().
"""
from __future__ import annotations

import logging
from http import HTTPStatus

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse

from ai.llm_provider import AIServiceError
from service_error import ServiceError

logger = logging.getLogger(__name__)


def _error_body(message: str, detail: str | None = None) -> dict:
    return {"error": {"message": message, "detail": detail}}


async def http_exception_handler(request: Request, exc: HTTPException) -> JSONResponse:
    if isinstance(exc.detail, dict) and "message" in exc.detail:
        return JSONResponse(
            status_code=exc.status_code, content=_error_body(exc.detail["message"], exc.detail.get("detail"))
        )
    return JSONResponse(status_code=exc.status_code, content=_error_body(str(exc.detail)))


async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    logger.exception("Unhandled error on %s %s", request.method, request.url.path)
    return JSONResponse(
        status_code=HTTPStatus.INTERNAL_SERVER_ERROR, content=_error_body("Internal server error.", str(exc))
    )

async def file_not_found_error_handler(request: Request, exc: FileNotFoundError) -> JSONResponse:
    error_msg = "\n".join(exc.args) if exc.args else "File not found"
    logger.exception(f"API: {request.method} {request.url.path}: {error_msg}", request.method, request.url.path)
    return JSONResponse(status_code=HTTPStatus.INTERNAL_SERVER_ERROR, content=_error_body(error_msg, f"API: {request.method} {request.url.path}"))

async def ai_service_error_handler(request: Request, exc: AIServiceError) -> JSONResponse:
    logger.exception("LLMProvider error on %s %s", request.method, request.url.path)
    return JSONResponse(status_code=exc.status_code, content=_error_body(exc.message, exc.detail))

async def service_error_handler(request: Request, exc: ServiceError) -> JSONResponse:
    """Covers every service-layer error — ChatServiceError, TrackingServiceError,
    any future subclass — in one handler: Starlette resolves an exception
    handler by walking the raised exception's own MRO, not by exact type
    match, so registering this for the base class alone is enough."""
    logger.exception("Service error on %s %s", request.method, request.url.path)
    return JSONResponse(status_code=exc.status_code, content=_error_body(exc.message, exc.detail))


def register_error_handlers(app: FastAPI) -> None:
    app.add_exception_handler(Exception, unhandled_exception_handler)
    app.add_exception_handler(HTTPException, http_exception_handler)
    app.add_exception_handler(FileNotFoundError, file_not_found_error_handler)
    app.add_exception_handler(AIServiceError, ai_service_error_handler)
    app.add_exception_handler(ServiceError, service_error_handler)
