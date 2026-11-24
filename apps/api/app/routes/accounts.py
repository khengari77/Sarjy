"""Account sign-up, sign-in, sign-out and session inspection."""

import json
from contextlib import contextmanager

from fastapi import Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.dependencies import ACCOUNT_COOKIE
from app.domain.accounts import (
    EMAIL_SOURCE_LIMIT,
    SESSION_TTL,
    SIGN_UP_LIMIT,
    SOURCE_LIMIT,
    AttemptLimit,
    acceptable_password,
    normalized_email,
)
from app.routes.reviewer import attempt_source
from app.services.account_auth import CredentialProvider, CredentialServiceUnavailable, CredentialsRejected
from app.services.accounts import (
    AccountAttemptLimit,
    AccountSessionRequired,
    attempt_key,
    authorize_account,
    close_session,
    forgive_attempts,
    open_session,
    record_attempts,
)

NO_STORE = {"Cache-Control": "no-store"}
MAX_REQUEST_BYTES = 4096
STATUS = {
    "ACCOUNT_CREDENTIALS_INVALID": 401,
    "ACCOUNT_EMAIL_UNCONFIRMED": 403,
    "ACCOUNT_EXISTS": 409,
    "ACCOUNT_PASSWORD_WEAK": 422,
    "ACCOUNT_INPUT_INVALID": 422,
}


class Credentials(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)

    email: str = Field(max_length=400)
    password: str = Field(max_length=400, repr=False)


def _error(status: int, code: str, headers: dict[str, str] | None = None) -> HTTPException:
    return HTTPException(status, detail={"code": code}, headers=NO_STORE | (headers or {}))


@contextmanager
def _answers():
    """Turn every expected failure into a coded response; nothing provider-supplied escapes."""
    try:
        yield
    except CredentialsRejected as exc:
        raise _error(STATUS[exc.code], exc.code) from None
    except AccountAttemptLimit as exc:
        raise _error(429, "ACCOUNT_ATTEMPT_LIMIT", {"Retry-After": str(exc.retry_after)}) from None
    except (CredentialServiceUnavailable, SQLAlchemyError):
        raise _error(503, "ACCOUNT_SERVICE_UNAVAILABLE") from None


async def _credentials(request: Request) -> tuple[str, str]:
    """Parse by hand: the framework's validation errors would echo the submitted password."""
    raw = bytearray()
    async for chunk in request.stream():
        raw.extend(chunk)
        if len(raw) > MAX_REQUEST_BYTES:
            raise _error(422, "ACCOUNT_INPUT_INVALID")
    try:
        body = Credentials.model_validate(json.loads(raw))
    except (ValueError, RecursionError):
        raise _error(422, "ACCOUNT_INPUT_INVALID") from None
    email = normalized_email(body.email)
    if email is None or not acceptable_password(body.password):
        raise _error(422, "ACCOUNT_INPUT_INVALID")
    return email, body.password


def _keyed(config: Settings, *attempts: tuple[AttemptLimit, str]) -> list[tuple[str, AttemptLimit]]:
    return [(attempt_key(config.web_service_secret, limit, value), limit) for limit, value in attempts]


def _set_session(response: JSONResponse, config: Settings, token: str) -> JSONResponse:
    response.set_cookie(
        ACCOUNT_COOKIE,
        token,
        httponly=True,
        secure=config.cookie_secure,
        samesite="lax",
        max_age=int(SESSION_TTL.total_seconds()),
        path="/",
    )
    return response


def register_account_routes(app, *, config: Settings, database, clock, credentials: CredentialProvider):
    async def start(request: Request, db: AsyncSession, auth_user_id: str, now) -> JSONResponse:
        try:
            identity, token = await open_session(
                db,
                auth_user_id=auth_user_id,
                previous_token=request.cookies.get(ACCOUNT_COOKIE, ""),
                now=now,
            )
        except AccountSessionRequired:
            raise CredentialsRejected("ACCOUNT_CREDENTIALS_INVALID") from None
        return _set_session(JSONResponse({"id": identity.id}, headers=NO_STORE), config, token)

    async def enter(request: Request, db: AsyncSession, provider, *, new: bool) -> JSONResponse:
        email, password = await _credentials(request)
        now, source = clock(), attempt_source(request, config)
        attempts = _keyed(config, (SOURCE_LIMIT, source), (EMAIL_SOURCE_LIMIT, f"{source} {email}"))
        created = _keyed(config, (SIGN_UP_LIMIT, source)) if new else []
        with _answers():
            await record_attempts(db, attempts + created, now)
            # No transaction stays open across the provider call.
            auth_user_id = await provider(email, password)
            await forgive_attempts(db, attempts, now)
            return await start(request, db, auth_user_id, now)

    @app.post("/v1/auth/sign-up")
    async def sign_up(request: Request, db: AsyncSession = Depends(database)):
        return await enter(request, db, credentials.sign_up, new=True)

    @app.post("/v1/auth/sign-in")
    async def sign_in(request: Request, db: AsyncSession = Depends(database)):
        return await enter(request, db, credentials.sign_in, new=False)

    @app.post("/v1/auth/sign-out")
    async def sign_out(request: Request, db: AsyncSession = Depends(database)):
        with _answers():
            await close_session(db, request.cookies.get(ACCOUNT_COOKIE, ""), clock())
        response = JSONResponse({"ok": True}, headers=NO_STORE)
        response.delete_cookie(ACCOUNT_COOKIE, path="/", httponly=True, secure=config.cookie_secure, samesite="lax")
        return response

    @app.get("/v1/auth/session")
    async def session(request: Request, db: AsyncSession = Depends(database)):
        with _answers():
            try:
                identity = await authorize_account(db, request.cookies.get(ACCOUNT_COOKIE, ""), clock())
            except AccountSessionRequired:
                raise _error(401, "ACCOUNT_SESSION_REQUIRED") from None
        return JSONResponse({"id": identity.id}, headers=NO_STORE)
