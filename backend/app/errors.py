"""Application exception handlers that avoid leaking database details."""

from __future__ import annotations

import logging
import uuid

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from sqlalchemy.exc import DatabaseError

log = logging.getLogger(__name__)


def register_database_error_handler(app: FastAPI) -> None:
    @app.exception_handler(DatabaseError)
    async def database_error_handler(
        request: Request, exc: DatabaseError
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
