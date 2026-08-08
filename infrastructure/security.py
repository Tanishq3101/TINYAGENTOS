# infrastructure/security.py

"""
Security utilities for TinyAgentOS.

Includes:
- Password hashing (bcrypt)
- Password verification
- JWT token generation
"""

from datetime import datetime, timedelta
from jose import jwt
from passlib.context import CryptContext

from infrastructure.config import get_settings

# Load configuration
settings = get_settings()

# Password hashing context (bcrypt is industry standard)
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

def create_access_token(data: dict) -> str:
    """
    Generate JWT access token.

    Steps:
    1. Copy input data
    2. Add expiration time
    3. Encode using secret key

    Returns:
    - Signed JWT token
    """

    to_encode = data.copy()

    expire = datetime.utcnow() + timedelta(
        minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES
    )

    # Add expiration field to token payload
    to_encode.update({"exp": expire})

    encoded_jwt = jwt.encode(
        to_encode,
        settings.SECRET_KEY,
        algorithm=settings.ALGORITHM
    )

    return encoded_jwt