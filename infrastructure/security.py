# infrastructure/security.py

"""
Security utilities: encryption at rest, API key handling, and request
signature verification.

Note: password hashing and JWT issuance live in infrastructure/auth.py,
not here — see that file's docstring for the split. This file owns only
symmetric encryption (Fernet), API key generation/hashing/verification,
and HMAC request-signature verification, for service-to-service /
programmatic auth.

Design notes (why this differs from a naive implementation):
- Fernet key is NEVER auto-generated in-process as a fallback. A silently
  generated key means encrypted data becomes unreadable the moment the
  process restarts, and it means two instances behind a load balancer
  would use different keys. The key must come from config/env, full stop.
- API keys are only ever stored hashed (SHA-256). The plaintext key is
  shown to the user exactly once, at generation time.
- Signature verification uses hmac.compare_digest to avoid timing attacks
  (a naive `==` comparison leaks how many leading bytes matched).
"""

from __future__ import annotations

import hashlib
import hmac
import secrets

from cryptography.fernet import Fernet, InvalidToken


class SecurityManager:
    """Handles encryption, API key management, and request-signature verification."""

    def __init__(self, encryption_key: str) -> None:
        if not encryption_key:
            raise ValueError(
                "SecurityManager requires an explicit encryption_key. "
                'Generate one with: python -c "from cryptography.fernet import Fernet; '
                'print(Fernet.generate_key().decode())" and store it in your secrets '
                "manager / .env — never auto-generate one at runtime."
            )
        self._cipher = Fernet(
            encryption_key.encode() if isinstance(encryption_key, str) else encryption_key
        )

    def encrypt_sensitive_data(self, data: str) -> str:
        """Encrypt a plaintext string for storage."""
        return self._cipher.encrypt(data.encode()).decode()

    def decrypt_sensitive_data(self, encrypted_data: str) -> str:
        """Decrypt a string previously produced by encrypt_sensitive_data.

        Raises InvalidToken if the ciphertext is malformed or the key is wrong —
        callers should treat that as a hard failure, never silently ignore it.
        """
        try:
            return self._cipher.decrypt(encrypted_data.encode()).decode()
        except InvalidToken as exc:
            raise ValueError("Failed to decrypt: invalid token or wrong key") from exc

    @staticmethod
    def hash_api_key(api_key: str) -> str:
        """Hash an API key for storage. Never store plaintext API keys."""
        return hashlib.sha256(api_key.encode()).hexdigest()

    @staticmethod
    def generate_api_key() -> str:
        """Generate a cryptographically secure random API key."""
        return secrets.token_urlsafe(32)

    @staticmethod
    def verify_api_key(candidate_key: str, stored_hash: str) -> bool:
        """Constant-time check of a presented API key against its stored hash."""
        candidate_hash = SecurityManager.hash_api_key(candidate_key)
        return hmac.compare_digest(candidate_hash, stored_hash)

    @staticmethod
    def verify_request_signature(request_body: str, signature: str, secret: str) -> bool:
        """Verify an HMAC-SHA256 signature for request integrity (e.g. webhooks)."""
        expected_sig = hmac.new(secret.encode(), request_body.encode(), hashlib.sha256).hexdigest()
        return hmac.compare_digest(signature, expected_sig)


def generate_fernet_key() -> str:
    """One-off helper for provisioning a new encryption key. Run manually, store the
    output in your secrets manager — do not call this from application code."""
    return Fernet.generate_key().decode()
