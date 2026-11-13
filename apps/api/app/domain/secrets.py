"""Constant-time comparison for configured shared secrets.

Header values, cookies and signed proposal tokens are attacker-controlled. A
plain ``hmac.compare_digest`` on two ``str`` values raises ``TypeError`` for
non-ASCII input, turning a malformed credential into a 500 instead of a
denial. Comparing UTF-8 bytes keeps every rejection closed and constant-time.
"""

import hmac


def secret_matches(supplied: object, expected: str) -> bool:
    """Return whether ``supplied`` equals the configured ``expected`` secret.

    Missing, malformed or unencodable values are denied without raising. An
    empty configured secret never matches, so an unset secret cannot authorize
    a caller.
    """
    if not isinstance(supplied, str) or not expected:
        return False
    try:
        submitted = supplied.encode("utf-8")
        configured = expected.encode("utf-8")
    except UnicodeEncodeError:
        return False
    return hmac.compare_digest(submitted, configured)
