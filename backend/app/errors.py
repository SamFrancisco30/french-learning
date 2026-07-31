"""Application exception handlers that avoid leaking database details."""

from __future__ import annotations

import logging
import uuid

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from sqlalchemy.exc import DBAPIError
from sqlalchemy.exc import TimeoutError as SQLAlchemyTimeoutError

log = logging.getLogger(__name__)


async def database_unavailable_handler(
    request: Request, exc: Exception
) -> JSONResponse:
    correlation_id = uuid.uuid4().hex
    log.error(
        "database failure class=%s correlation_id=%s path=%s",
        type(exc).__name__,
        correlation_id,
        request.url.path,
    )
    return JSONResponse(
        status_code=503,
        content={"detail": "database temporarily unavailable"},
        headers={"X-Correlation-ID": correlation_id},
    )


def register_database_error_handler(app: FastAPI) -> None:
    app.add_exception_handler(DBAPIError, database_unavailable_handler)
    app.add_exception_handler(SQLAlchemyTimeoutError, database_unavailable_handler)
