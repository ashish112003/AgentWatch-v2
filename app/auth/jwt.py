"""
app/auth/jwt.py
────────────────
JWT token creation and verification.

Token anatomy:
  ┌─────────────────────────────────────────────────────────────┐
  │  Header   {"alg": "HS256", "typ": "JWT"}                   │
  │  Payload  {"sub": "<agent_uuid>",                          │
  │             "agent_name": "<name>",                         │
  │             "iat": <unix_ts>,                               │
  │             "exp": <unix_ts>}                               │
  │  Signature  HMAC-SHA256(base64(header) + "." +             │
  │             base64(payload), JWT_SECRET_KEY)                │
  └─────────────────────────────────────────────────────────────┘

Security notes:
  • We use HS256 (symmetric HMAC) because all token verification
    happens inside the same service.  For multi-service architectures,
    switch to RS256 (asymmetric) so other services can verify without
    the private key.
  • The secret key length should be ≥ 256 bits (32 bytes).  The
    .env.example shows how to generate one with `openssl rand -hex 32`.
  • JWTs are NOT encrypted — the payload is base64url-encoded but
    readable by anyone.  Never put sensitive data (passwords, PII)
    in the payload.
  • We store only agent_id (sub) and agent_name.  Permissions are
    looked up from the database on each request, not cached in the
    token — this ensures revoked/updated permissions take effect
    immediately without waiting for token expiry.

Usage:
    token = create_access_token(agent_id="uuid-...", agent_name="my-bot")
    payload = decode_access_token(token)
    print(payload.sub)  # "uuid-..."
"""

from datetime import datetime, timedelta, timezone

from jose import JWTError, jwt
from jose.exceptions import ExpiredSignatureError

from app.core.config import settings
from app.schemas.token import TokenPayload


def create_access_token(
    agent_id: str,
    agent_name: str,
    expires_delta: timedelta | None = None,
) -> str:
    """
    Mint a signed JWT access token for the given agent.

    The token lifetime defaults to JWT_ACCESS_TOKEN_EXPIRE_MINUTES from
    settings.  Pass `expires_delta` to override (useful in tests).

    Args:
        agent_id:      UUID of the agent (stored as the JWT `sub` claim).
        agent_name:    Display name baked into the token payload.
        expires_delta: Custom token lifetime.  Defaults to settings value.

    Returns:
        A signed JWT string ready to send in an Authorization header.
    """
    # Always use UTC to avoid timezone ambiguity across servers.
    now = datetime.now(tz=timezone.utc)

    if expires_delta is not None:
        expire = now + expires_delta
    else:
        expire = now + timedelta(minutes=settings.JWT_ACCESS_TOKEN_EXPIRE_MINUTES)

    payload: dict = {
        # Standard JWT claims
        "sub": agent_id,          # Subject — who the token represents
        "exp": expire,            # Expiry  — python-jose handles UTC datetime → Unix ts
        "iat": now,               # Issued at

        # Custom private claims (namespaced to avoid collisions)
        "agent_name": agent_name,
    }

    # jose.jwt.encode() signs the payload using HMAC-SHA256 and the secret key.
    # The result is a compact three-part string: header.payload.signature
    token: str = jwt.encode(
        payload,
        settings.JWT_SECRET_KEY,
        algorithm=settings.JWT_ALGORITHM,
    )
    return token


def decode_access_token(token: str) -> TokenPayload:
    """
    Decode and validate a JWT access token.

    Validates:
      • Signature — rejects tokens signed with a different key.
      • Expiry    — rejects tokens past their `exp` claim.
      • Algorithm — rejects tokens using unexpected algorithms
                    (prevents the "none" algorithm attack).

    Args:
        token: Raw JWT string from the Authorization header.

    Returns:
        A validated TokenPayload instance.

    Raises:
        ExpiredSignatureError: Token has expired.
        JWTError:              Token is malformed or has an invalid signature.
    """
    # Explicitly whitelist the expected algorithm.
    # If someone sends a token signed with "none" or RS256,
    # python-jose raises JWTError before we ever read the payload.
    decoded: dict = jwt.decode(
        token,
        settings.JWT_SECRET_KEY,
        algorithms=[settings.JWT_ALGORITHM],
    )

    # Validate the decoded dict against our typed schema.
    # This will raise a ValidationError if `sub` is missing, which
    # the calling dependency converts to a 401 response.
    return TokenPayload(**decoded)


def decode_access_token_safe(token: str) -> TokenPayload | None:
    """
    Decode a JWT, returning None instead of raising on any error.

    Convenience wrapper for places where we want a boolean check
    without try/except at the call site (e.g. optional auth middleware).

    Returns:
        TokenPayload on success, None on any failure.
    """
    try:
        return decode_access_token(token)
    except (JWTError, ExpiredSignatureError, Exception):
        return None