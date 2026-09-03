"""The uniform {error: {message, detail}} contract every endpoint's
failures share — registered once, in main.py, via ApiErrorHandlers.register().
"""
from __future__ import annotations

from http import HTTPStatus

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse

from ai import AIServiceError
from logging_factory import LoggerFactory
from service_error import ServiceError

logger = LoggerFactory.get_logger(__name__)


class ApiErrorHandlers:
    """FastAPI exception handlers for every error type an endpoint can
    raise, each normalized to the same {error: {message, detail}} body."""

    @staticmethod
    def _body(message: str, detail: str | None = None, code: str | None = None) -> dict:
        body = {"error": {"message": message, "detail": detail}}
        if code is not None:
            body["error"]["code"] = code
        return body

    @classmethod
    async def http_exception(cls, request: Request, exc: HTTPException) -> JSONResponse:
        if isinstance(exc.detail, dict) and "message" in exc.detail:
            return JSONResponse(
                status_code=exc.status_code, content=cls._body(exc.detail["message"], exc.detail.get("detail"))
            )
        return JSONResponse(status_code=exc.status_code, content=cls._body(str(exc.detail)))

    @classmethod
    async def unhandled_exception(cls, request: Request, exc: Exception) -> JSONResponse:
        logger.exception("Unhandled error on %s %s", request.method, request.url.path)
        return JSONResponse(
            status_code=HTTPStatus.INTERNAL_SERVER_ERROR, content=cls._body("Internal server error.", str(exc))
        )

    @classmethod
    async def file_not_found_error(cls, request: Request, exc: FileNotFoundError) -> JSONResponse:
        error_msg = "\n".join(exc.args) if exc.args else "File not found"
        logger.exception(f"API: {request.method} {request.url.path}: {error_msg}")
        return JSONResponse(status_code=HTTPStatus.INTERNAL_SERVER_ERROR, content=cls._body(error_msg, f"API: {request.method} {request.url.path}"))

    @classmethod
    async def ai_service_error(cls, request: Request, exc: AIServiceError) -> JSONResponse:
        logger.exception("LLMProvider error on %s %s", request.method, request.url.path)
        return JSONResponse(status_code=exc.status_code, content=cls._body(exc.message, exc.detail))

    @classmethod
    async def service_error(cls, request: Request, exc: ServiceError) -> JSONResponse:
        """Covers every service-layer error — ChatServiceError, TrackingServiceError,
        any future subclass — in one handler, since Starlette resolves handlers
        by walking the exception's MRO rather than requiring an exact type match."""
        logger.exception("Service error on %s %s", request.method, request.url.path)
        return JSONResponse(status_code=exc.status_code, content=cls._body(exc.message, exc.detail, exc.code))

    @classmethod
    def register(cls, app: FastAPI) -> None:
        app.add_exception_handler(Exception, cls.unhandled_exception)
        app.add_exception_handler(HTTPException, cls.http_exception)
        app.add_exception_handler(FileNotFoundError, cls.file_not_found_error)
        app.add_exception_handler(AIServiceError, cls.ai_service_error)
        app.add_exception_handler(ServiceError, cls.service_error)
