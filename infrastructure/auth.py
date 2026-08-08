# infrastructure/auth.py

"""
Password hashing and JWT session-token handling for TinyAgentOS.

Distinct from infrastructure/security.py:
- security.py: symmetric encryption (Fernet) + API key hashing/verification +
  HMAC request signatures — for service-to-service / programmatic auth.
- auth.py (this file): user password hashing + JWT issuance/verification —
  for human login flows (e.g. an admin panel, if one is added later).
"""

from datetime import datetime, timedelta, timezone
from typing import Any

from jose import JWTError, jwt
from passlib.context import CryptContext

from infrastructure.config import get_settings

settings = get_settings()

# Password hashing context (bcrypt is industry standard).
# Note: bcrypt silently truncates input past 72 bytes — not a bug, just a
# known limitation of the algorithm. Fine for normal passwords.
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


# -------------------------
# Password Handling
# -------------------------

def hash_password(password: str) -> str:
    """
    Hash a plain-text password.

    Why:
    - Never store raw passwords
    - Protect against database leaks
    """
    return pwd_context.hash(password)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """
    Verify a password against its hash.

    Returns:
    - True if match
    - False otherwise
    """
    return pwd_context.verify(plain_password, hashed_password)


# -------------------------
# JWT Token Handling
# -------------------------

def create_access_token(data: dict, expires_delta: timedelta | None = None) -> str:
    """
    Generate a signed JWT access token.

    Steps:
    1. Copy input data (never mutate the caller's dict)
    2. Add expiration time
    3. Encode using the app secret key

    Args:
        data: claims to embed (e.g. {"sub": user_id}). Must not rely on an
            "exp" key already being present — it will be overwritten.
        expires_delta: override the default expiry from settings.

    Returns:
        Signed JWT token string.
    """
    to_encode = data.copy()

    expire = datetime.now(timezone.utc) + (
        expires_delta or timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    )
    to_encode.update({"exp": expire})

    return jwt.encode(to_encode, settings.SECRET_KEY, algorithm=settings.ALGORITHM)


def decode_access_token(token: str) -> dict[str, Any] | None:
    """
    Verify and decode a JWT access token.

    Returns:
    - The decoded claims dict if the token is valid and unexpired
    - None if the token is invalid, tampered with, or expired

    Callers (e.g. auth middleware) should treat None as "reject the request",
    not raise an unhandled exception up to the client.
    """
    try:
        return jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
    except JWTError:
        return None