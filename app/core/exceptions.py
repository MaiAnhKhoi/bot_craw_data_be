"""Lỗi nghiệp vụ + handler trả đúng phong bì ApiResponse."""
from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from loguru import logger
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.response import ApiResponse


class AppError(Exception):
    """Lỗi nghiệp vụ có mã ổn định để FE bắt được."""

    status_code = 400
    code = "APP_ERROR"

    def __init__(self, message: str, *, code: str | None = None, status_code: int | None = None) -> None:
        super().__init__(message)
        self.message = message
        if code:
            self.code = code
        if status_code:
            self.status_code = status_code


class NotFoundError(AppError):
    status_code = 404
    code = "NOT_FOUND"


class UnauthorizedError(AppError):
    status_code = 401
    code = "UNAUTHORIZED"


class ConflictError(AppError):
    status_code = 409
    code = "CONFLICT"


def _payload(code: str, message: str) -> dict:
    return ApiResponse.fail(code, message).model_dump()


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def _app_error(_: Request, exc: AppError):  # noqa: ANN202
        return JSONResponse(status_code=exc.status_code, content=_payload(exc.code, exc.message))

    @app.exception_handler(RequestValidationError)
    async def _validation(_: Request, exc: RequestValidationError):  # noqa: ANN202
        first = exc.errors()[0] if exc.errors() else {}
        loc = ".".join(str(p) for p in first.get("loc", [])[1:]) or "body"
        return JSONResponse(
            status_code=422,
            content=_payload("VALIDATION_ERROR", f"{loc}: {first.get('msg', 'dữ liệu không hợp lệ')}"),
        )

    @app.exception_handler(StarletteHTTPException)
    async def _http(_: Request, exc: StarletteHTTPException):  # noqa: ANN202
        code = {401: "UNAUTHORIZED", 403: "FORBIDDEN", 404: "NOT_FOUND"}.get(exc.status_code, "HTTP_ERROR")
        return JSONResponse(status_code=exc.status_code, content=_payload(code, str(exc.detail)))

    @app.exception_handler(Exception)
    async def _unhandled(_: Request, exc: Exception):  # noqa: ANN202
        logger.exception("Lỗi không lường trước: {}", exc)
        return JSONResponse(status_code=500, content=_payload("INTERNAL_ERROR", "Lỗi hệ thống"))
