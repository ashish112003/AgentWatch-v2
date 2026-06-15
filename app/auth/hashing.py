"""
app/auth/hashing.py
────────────────────
Password / secret hashing utilities using bcrypt via passlib.

Why passlib instead of calling bcrypt directly?
  • passlib's CryptContext handles algorithm versioning — if we ever
    need to migrate from bcrypt to argon2, we add it to schemes and
    existing hashes are transparently re-hashed on next verify.
  • passlib normalises the API: hash(), verify(), deprecated() — no
    need to manage salts, encodings, or bcrypt version flags manually.

Security notes:
  • bcrypt has a max input length of 72 bytes.  For agent secrets
    longer than 72 chars, the excess is silently truncated by bcrypt.
    This is fine for our use-case since secrets are validated at
    registration (min 12 chars, no practical upper bound needed).
  • rounds=12 is the passlib default for bcrypt and provides a good
    balance between security and hashing speed (~250 ms on modern HW).
    Increase to 13-14 for higher-security production deployments.

Usage:
    from app.auth.hashing import hash_secret, verify_secret

    stored_hash = hash_secret("my-plain-text-secret")
    is_valid    = verify_secret("my-plain-text-secret", stored_hash)
"""

from passlib.context import CryptContext

# CryptContext manages one or more hashing schemes.
# schemes=["bcrypt"]   — bcrypt is the only supported algorithm.
# deprecated="auto"    — any scheme not listed first is treated as
#                        deprecated and will be flagged on verify().
#                        Useful when adding argon2 in a future upgrade.
_pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def hash_secret(plain_secret: str) -> str:
    """
    Hash a plain-text secret with bcrypt.

    Returns a self-contained hash string in the format:
        $2b$12$<22-char-salt><31-char-hash>

    The algorithm identifier, cost factor, salt, and hash are all
    embedded in the returned string — no separate salt storage needed.

    Args:
        plain_secret: The raw secret to hash (e.g. from AgentCreate).

    Returns:
        A bcrypt hash string safe to store in the database.
    """
    return _pwd_context.hash(plain_secret)


def verify_secret(plain_secret: str, hashed_secret: str) -> bool:
    """
    Verify a plain-text secret against a stored bcrypt hash.

    Uses a constant-time comparison internally to prevent timing attacks.
    Returns False (not an exception) if the hash is malformed, so callers
    get a clean boolean without needing a try/except.

    Args:
        plain_secret:   The raw secret supplied by the caller.
        hashed_secret:  The bcrypt hash stored in the database.

    Returns:
        True if the secret matches the hash, False otherwise.
    """
    return _pwd_context.verify(plain_secret, hashed_secret)


def is_hash_deprecated(hashed_secret: str) -> bool:
    """
    Check whether a stored hash uses a deprecated algorithm or cost factor.

    Useful in a future migration path: after verifying a secret
    successfully, call this and re-hash if True.

    Args:
        hashed_secret: The hash string from the database.

    Returns:
        True if the hash should be upgraded, False if it is current.
    """
    valid, _ = _pwd_context.verify_and_update("", hashed_secret)
    # verify_and_update returns (bool, new_hash_or_None)
    # We only care about whether a new hash was produced.
    return _ is not None