"""Supabase Auth as the credential store. Nothing here persists or logs a credential."""

import logging
from typing import Any, Final, Protocol

import httpx

logger = logging.getLogger(__name__)

TIMEOUT_SECONDS: Final = 8.0
MAX_RESPONSE_BYTES: Final = 64 * 1024


class CredentialsRejected(Exception):
    """The provider answered, and the answer is no. ``code`` is one of ours, never the provider's text."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class CredentialServiceUnavailable(Exception):
    """No usable answer: timeout, transport failure, throttling or an unexpected body."""


class CredentialProvider(Protocol):
    async def sign_up(self, email: str, password: str) -> str: ...

    async def sign_in(self, email: str, password: str) -> str: ...

    async def exchange_code(self, code: str, verifier: str) -> str: ...


SIGN_UP_CODES: Final = {
    "user_already_exists": "ACCOUNT_EXISTS",
    "email_exists": "ACCOUNT_EXISTS",
    "weak_password": "ACCOUNT_PASSWORD_WEAK",
    "email_address_invalid": "ACCOUNT_INPUT_INVALID",
    "validation_failed": "ACCOUNT_INPUT_INVALID",
}
SIGN_IN_CODES: Final = {
    "invalid_credentials": "ACCOUNT_CREDENTIALS_INVALID",
    "invalid_grant": "ACCOUNT_CREDENTIALS_INVALID",
    "email_not_confirmed": "ACCOUNT_EMAIL_UNCONFIRMED",
    "user_banned": "ACCOUNT_CREDENTIALS_INVALID",
}
# A code that is wrong, spent or expired is the visitor's failure; anything else is ours or the provider's.
OAUTH_CODES: Final = {
    "validation_failed": "ACCOUNT_OAUTH_FAILED",
    "bad_code_verifier": "ACCOUNT_OAUTH_FAILED",
    "flow_state_not_found": "ACCOUNT_OAUTH_FAILED",
    "flow_state_expired": "ACCOUNT_OAUTH_FAILED",
    "bad_oauth_callback": "ACCOUNT_OAUTH_FAILED",
    "invalid_grant": "ACCOUNT_OAUTH_FAILED",
    "user_banned": "ACCOUNT_OAUTH_FAILED",
}


class SupabaseCredentials:
    def __init__(
        self, base_url: str, publishable_key: str, *, transport: httpx.AsyncBaseTransport | None = None
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._key = publishable_key
        self._transport = transport

    async def sign_up(self, email: str, password: str) -> str:
        body = await self._post("/auth/v1/signup", email, password, SIGN_UP_CODES)
        # Without a session the project still requires email confirmation.
        if not isinstance(body.get("access_token"), str):
            raise CredentialsRejected("ACCOUNT_EMAIL_UNCONFIRMED")
        return _user_id(body)

    async def sign_in(self, email: str, password: str) -> str:
        return _user_id(await self._post("/auth/v1/token?grant_type=password", email, password, SIGN_IN_CODES))

    async def exchange_code(self, code: str, verifier: str) -> str:
        """Trade a PKCE authorization code from a sign-in with a provider for the account's id."""
        body = {"auth_code": code, "code_verifier": verifier}
        user = await self._send("/auth/v1/token?grant_type=pkce", body, OAUTH_CODES)
        # Supabase auto-links a provider identity to a user with the same email.
        # An account that also registered a password must use that password.
        if _linked_email_and_provider(user):
            raise CredentialsRejected("ACCOUNT_OAUTH_EMAIL_TAKEN")
        return _user_id(user)

    async def _post(self, path: str, email: str, password: str, codes: dict[str, str]) -> dict[str, Any]:
        return await self._send(path, {"email": email, "password": password}, codes)

    async def _send(self, path: str, payload: dict[str, str], codes: dict[str, str]) -> dict[str, Any]:
        try:
            async with httpx.AsyncClient(
                transport=self._transport, timeout=TIMEOUT_SECONDS, follow_redirects=False
            ) as client:
                response = await client.post(self._base_url + path, headers={"apikey": self._key}, json=payload)
        except httpx.HTTPError as exc:
            logger.warning("account provider unreachable: %s", type(exc).__name__)
            raise CredentialServiceUnavailable from None
        body = _object(response)
        if response.status_code == 200 and body is not None:
            return body
        error = body.get("error_code") or body.get("error") if body is not None else None
        code = codes.get(error) if isinstance(error, str) else None
        if code is not None and response.status_code in {400, 401, 403, 409, 422}:
            raise CredentialsRejected(code)
        logger.warning("account provider answered HTTP %s", response.status_code)
        raise CredentialServiceUnavailable


def _object(response: httpx.Response) -> dict[str, Any] | None:
    if len(response.content) > MAX_RESPONSE_BYTES:
        return None
    try:
        body = response.json()
    except ValueError:
        return None
    return body if isinstance(body, dict) else None


def _linked_email_and_provider(body: dict[str, Any]) -> bool:
    user = body.get("user")
    identities = user.get("identities") if isinstance(user, dict) else None
    if not isinstance(identities, list):
        return False
    providers = {
        identity["provider"]
        for identity in identities
        if isinstance(identity, dict) and isinstance(identity.get("provider"), str)
    }
    return "email" in providers and len(providers) > 1


def _user_id(body: dict[str, Any]) -> str:
    user = body.get("user")
    value = user.get("id") if isinstance(user, dict) else None
    if not isinstance(value, str) or not 1 <= len(value) <= 64:
        raise CredentialServiceUnavailable
    return value


__all__ = ["CredentialProvider", "CredentialServiceUnavailable", "CredentialsRejected", "SupabaseCredentials"]
