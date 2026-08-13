"""AES-256-GCM encryption for MCP client secrets.

The 32-byte key is derived from the ``MCP_CLIENT_SECRET_ENCRYPTION_KEY``
environment variable via SHA-256, so any passphrase works while the actual
AES key stays fixed-length.

Ciphertext format::

    v1:<base64(nonce(12) || ciphertext||tag)>

The ``v1:`` prefix leaves room for algorithm rotation in the future.
"""

from __future__ import annotations

import base64
import hashlib
import os
import secrets

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from app.core.config import settings

_FORMAT_PREFIX = "v1:"


class McpSecretCryptoError(ValueError):
    """Raised when a secret cannot be decrypted (wrong key or tampered data)."""


def _aes_key() -> bytes:
    """Derive the 32-byte AES key from the configured master key."""
    raw = (settings.mcp_client_secret_encryption_key or "").encode("utf-8")
    if not raw:
        raise McpSecretCryptoError(
            "MCP_CLIENT_SECRET_ENCRYPTION_KEY 未配置，无法加解密 Client Secret"
        )
    return hashlib.sha256(raw).digest()


def generate_client_id() -> str:
    """Generate a globally unique client id, e.g. ``kr_9f2c...``."""
    return "kr_" + secrets.token_hex(16)


def generate_client_secret() -> str:
    """Generate a fresh random client secret (urlsafe base64, 43 chars)."""
    return secrets.token_urlsafe(32)


def encrypt_secret(plaintext: str) -> str:
    """Encrypt a client secret for storage.  Returns the v1: base64 blob."""
    nonce = os.urandom(12)
    ciphertext = AESGCM(_aes_key()).encrypt(nonce, plaintext.encode("utf-8"), None)
    return _FORMAT_PREFIX + base64.b64encode(nonce + ciphertext).decode("ascii")


def decrypt_secret(blob: str) -> str:
    """Decrypt a stored client secret.

    Raises :class:`McpSecretCryptoError` on wrong key / tampered ciphertext.
    """
    if not blob or not blob.startswith(_FORMAT_PREFIX):
        raise McpSecretCryptoError("Client Secret 密文格式无效")
    try:
        raw = base64.b64decode(blob[len(_FORMAT_PREFIX):], validate=True)
    except Exception as exc:
        raise McpSecretCryptoError("Client Secret 密文损坏") from exc
    if len(raw) < 12 + 16:  # nonce + tag minimum
        raise McpSecretCryptoError("Client Secret 密文损坏")
    nonce, ciphertext = raw[:12], raw[12:]
    try:
        plaintext = AESGCM(_aes_key()).decrypt(nonce, ciphertext, None)
    except Exception as exc:
        raise McpSecretCryptoError(
            "Client Secret 解密失败（Master Key 不匹配或密文被篡改）"
        ) from exc
    return plaintext.decode("utf-8")
