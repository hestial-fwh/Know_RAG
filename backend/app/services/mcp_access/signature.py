"""HMAC-SHA256 request signing for MCP signed access.

This module is the single canonicalization implementation.  The MCP auth
middleware does not compute signatures at all — it forwards the raw header
values to the backend verify endpoint, and the backend (this module) decides.

The test client script ``backend/scripts/test_mcp_signed_client.py`` imports
this module as well, so client and server can never drift apart.

Canonical string (six lines joined by ``\\n``, no trailing newline)::

    {client_id}
    {timestamp}
    {nonce}
    {METHOD}
    {path}
    {body_sha256}

signature = lowercase hex of HMAC-SHA256(key=client_secret, msg=canonical)
"""

from __future__ import annotations

import hashlib
import hmac


def sha256_hex(data: bytes) -> str:
    """Lowercase hex SHA-256 of raw bytes (empty body hashes to the well-known
    e3b0c442... value)."""
    return hashlib.sha256(data).hexdigest()


def build_canonical_string(
    *,
    client_id: str,
    timestamp: str,
    nonce: str,
    method: str,
    path: str,
    body_sha256: str,
) -> str:
    """Build the canonical string that both client and server sign/verify."""
    return "\n".join(
        [
            client_id,
            timestamp,
            nonce,
            (method or "").upper(),
            path or "",
            body_sha256 or "",
        ]
    )


def compute_signature(
    *,
    client_secret: str,
    client_id: str,
    timestamp: str,
    nonce: str,
    method: str,
    path: str,
    body_sha256: str,
) -> str:
    """Compute the lowercase hex HMAC-SHA256 signature for a request."""
    canonical = build_canonical_string(
        client_id=client_id,
        timestamp=timestamp,
        nonce=nonce,
        method=method,
        path=path,
        body_sha256=body_sha256,
    )
    return hmac.new(
        client_secret.encode("utf-8"),
        canonical.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def verify_signature(
    *,
    client_secret: str,
    client_id: str,
    timestamp: str,
    nonce: str,
    method: str,
    path: str,
    body_sha256: str,
    signature: str,
) -> bool:
    """Constant-time comparison of a request signature."""
    expected = compute_signature(
        client_secret=client_secret,
        client_id=client_id,
        timestamp=timestamp,
        nonce=nonce,
        method=method,
        path=path,
        body_sha256=body_sha256,
    )
    return hmac.compare_digest(expected, (signature or "").strip().lower())
