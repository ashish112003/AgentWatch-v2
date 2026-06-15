"""
app/schemas/token.py
─────────────────────
Pydantic v2 schemas for JWT authentication.

Separation of concerns:
  • TokenPayload  — the decoded claims we store INSIDE the JWT itself.
  • TokenResponse — the HTTP response body after a successful registration
                    or future login endpoint.

Why a separate TokenPayload schema?
  JWT claims are encoded/decoded in app/auth/jwt.py.  Having a typed
  schema means we get validation and IDE auto-complete when working
  with the decoded dict, instead of raw string-key lookups.

  Standard JWT reserved claims used:
    sub  (subject)  — the agent's UUID
    exp  (expiry)   — Unix timestamp after which the token is rejected
    iat  (issued at)— Unix timestamp when the token was minted
  Custom private claims:
    agent_name      — human-readable name, avoids a DB round-trip in
                      simple middleware checks
"""

from pydantic import BaseModel, Field
from datetime import datetime


class TokenPayload(BaseModel):
    """
    Claims decoded from a JWT.

    All fields are Optional because Pydantic is used to validate the
    decoded dict — a malformed token may be missing fields, and we want
    a clean ValidationError rather than a KeyError crash.
    """

    # Standard claims
    sub: str | None = Field(
        default=None,
        description="Subject — the agent UUID this token was issued for.",
    )
    exp: datetime | None = Field(
        default=None,
        description="Expiry timestamp.  python-jose validates this automatically.",
    )
    iat: datetime | None = Field(
        default=None,
        description="Issued-at timestamp.",
    )

    # Custom private claim
    agent_name: str | None = Field(
        default=None,
        description="Agent display name baked into the token payload.",
    )


class TokenResponse(BaseModel):
    """
    HTTP response body returned after a successful agent registration.

    Clients should:
      1. Store agent_id for later API calls (e.g. GET /audit/logs/{agent_id}).
      2. Pass access_token in the Authorization header for protected routes:
             Authorization: Bearer <access_token>
    """

    agent_id: str = Field(description="UUID of the newly registered agent.")
    access_token: str = Field(description="Signed JWT bearer token.")
    token_type: str = Field(
        default="bearer",
        description="OAuth2 token type — always 'bearer' for this API.",
    )

    model_config = {
        "json_schema_extra": {
            "example": {
                "agent_id": "3fa85f64-5717-4562-b3fc-2c963f66afa6",
                "access_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
                "token_type": "bearer",
            }
        }
    }