"""Reviewer invitation exchange and sanitized session inspection."""

import ipaddress
import json

from fastapi import Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.dependencies import COOKIE
from app.domain.secrets import secret_matches
from app.services.reviewer_access import (
    IDENTITY_TTL,
    ReviewerAccessDenied,
    ReviewerLimitExceeded,
    ReviewerSessionRequired,
    admit,
    authorize,
    record_attempt,
    valid_token_shape,
)

MAX_REQUEST_BYTES = 1024
DENIED = {"code": "REVIEWER_ACCESS_DENIED"}
NO_STORE = {"Cache-Control": "no-store"}


def _denied() -> HTTPException:
    return HTTPException(403, detail=DENIED)


async def _bounded_body(request: Request) -> bytes:
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > MAX_REQUEST_BYTES:
            raise HTTPException(413, detail={"code": "REVIEWER_REQUEST_TOO_LARGE"})
    return bytes(body)


async def _invitation_token(request: Request) -> str:
    raw = await _bounded_body(request)
    try:
        payload = json.loads(raw)
    except (ValueError, UnicodeDecodeError):
        raise _denied() from None
    if not isinstance(payload, dict) or set(payload) != {"invitation_token"}:
        raise _denied()
    token = payload["invitation_token"]
    if not valid_token_shape(token):
        raise _denied()
    return token


def _limit_error(exc: ReviewerLimitExceeded) -> HTTPException:
    return HTTPException(
        429, detail={"code": "REVIEWER_ADMISSION_LIMIT"}, headers={"Retry-After": str(exc.retry_after)}
    )


def _unavailable() -> HTTPException:
    return HTTPException(503, detail={"code": "REVIEWER_UNAVAILABLE"})


def attempt_source(request: Request, config: Settings) -> str:
    """Return a bounded per-source key for the admission limiter.

    The web proxy holds the web-service secret and overwrites
    ``x-reviewer-source`` from the socket peer, so only the proxy can set it.
    Missing, malformed or spoofed values fall back to the immediate peer
    address instead of creating an attacker-chosen bucket.
    """
    peer = request.client.host if request.client is not None else "unknown"
    supplied = request.headers.get("x-reviewer-source", "")
    if supplied and secret_matches(request.headers.get("x-web-service-secret", ""), config.web_service_secret):
        try:
            return str(ipaddress.ip_address(supplied.strip()))
        except ValueError:
            return peer
    return peer


def register_reviewer_routes(app, *, config: Settings, database, clock):
    @app.post("/v1/reviewer/session")
    async def create_reviewer_session(request: Request, db: AsyncSession = Depends(database)):
        token = await _invitation_token(request)
        now = clock()
        try:
            await record_attempt(db, now, attempt_source(request, config))
        except ReviewerLimitExceeded as exc:
            raise _limit_error(exc) from None
        except SQLAlchemyError:
            raise _unavailable() from None
        try:
            admitted = await admit(db, request.cookies.get(COOKIE, ""), token, now)
        except ReviewerAccessDenied:
            raise _denied() from None
        except ReviewerLimitExceeded as exc:
            raise _limit_error(exc) from None
        except SQLAlchemyError:
            raise _unavailable() from None
        response = JSONResponse(
            {"id": admitted.identity.id, "expires_at": admitted.identity.expires_at},
            headers=NO_STORE,
        )
        if admitted.cookie_token is not None:
            response.set_cookie(
                COOKIE,
                admitted.cookie_token,
                httponly=True,
                secure=config.cookie_secure,
                samesite="lax",
                max_age=int(IDENTITY_TTL.total_seconds()),
                path="/",
            )
        return response

    @app.get("/v1/reviewer/session")
    async def read_reviewer_session(request: Request, db: AsyncSession = Depends(database)):
        try:
            identity = await authorize(db, request.cookies.get(COOKIE, ""), clock())
        except ReviewerSessionRequired:
            raise HTTPException(401, detail={"code": "REVIEWER_SESSION_REQUIRED"}) from None
        except ReviewerAccessDenied:
            raise _denied() from None
        except SQLAlchemyError:
            raise _unavailable() from None
        return JSONResponse({"id": identity.id, "expires_at": identity.expires_at}, headers=NO_STORE)
