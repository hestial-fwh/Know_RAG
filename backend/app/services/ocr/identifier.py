"""Generic OCR identifier normaliser.

Only identifier-like tokens are processed — the rest of the text passes
through unchanged.  Only **safe** substitutions are applied globally:

* ``O`` / ``U`` → ``0`` — but only when adjacent to a digit (avoids
  corrupting English words like ``ORDER``, ``ANCHOR``, ``INVOICE``).

``I`` → ``1``, ``L`` → ``1``, ``S`` → ``5`` are NOT applied in the
generic path because they are often correct in uppercase prefixes
(e.g. ``INV``, ``LINE``, ``SCAN``).  Template-specific normalisers may
add these where the identifier format is known.
"""

from __future__ import annotations

import re
from typing import Any

from app.utils.text import sanitize_text_for_postgres

# ── Identifier candidate pattern ───────────────────────────────────────
#  e.g. INV-001-00, CN-ANCHOR-003-00, KB-2026-0608, AB1234, 1NV-UU1-OO
_ID_CANDIDATE_RE = re.compile(
    r"\b"
    r"(?:[A-Za-z0-9]{2,8}[-_./]?)"  # prefix (optional hyphen/dot)
    r"[A-Za-z0-9]{1,6}(?:[-_./][A-Za-z0-9]{1,6}){0,3}"  # body with separators
    r"\b",
    re.I,
)

# ── Prefix normalisation (common OCR) ──────────────────────────────────
#  [1IL]NV → INV is a near-universal OCR issue in document processing
_PREFIX_INV_RE = re.compile(r"^[1IL]NV", re.I)


def _is_likely_identifier(token: str) -> bool:
    """Heuristic: an identifier has mixed alpha+digit and at least 3 chars."""
    if len(token) < 3:
        return False
    has_alpha = bool(re.search(r"[A-Za-z]", token))
    has_digit = bool(re.search(r"[0-9]", token))
    return has_alpha and has_digit


def _normalize_identifier_token(token: str) -> str:
    """Apply safe OCR confusion mapping to one identifier token.

    Strategy
        1.  Normalise ``[1IL]NV`` → ``INV`` (common prefix OCR glitch).
        2.  Replace ``O`` / ``U`` with ``0`` ONLY in positions adjacent to
            a digit (within 2 characters).  This avoids corrupting English
            words like ``ORDER``, ``ANCHOR``, ``INVOICE``.

    ``I`` → ``1``, ``L`` → ``1``, ``S`` → ``5`` are deliberately excluded
    from the generic path.
    """
    upper = token.upper()
    confusable = sum(1 for ch in upper if ch in "OU")
    if confusable == 0:
        return token

    # Step 1: context-sensitive O/U → 0 only when near a digit
    chars = list(upper)
    for i, ch in enumerate(chars):
        if ch in "OU":
            left = chars[max(0, i - 2) : i]
            right = chars[i + 1 : min(len(chars), i + 3)]
            context = "".join(left + right)
            if re.search(r"[0-9]", context):
                chars[i] = "0"

    result = "".join(chars)

    # Step 2: [1IL]NV → INV prefix normalisation
    result = _PREFIX_INV_RE.sub("INV", result)

    if result == upper:
        return token
    return result


def normalize_identifiers(text: str, **kwargs: Any) -> str:
    """Normalise OCR-corrupted identifier tokens while leaving prose alone.

    Only tokens matching the identifier pattern AND containing both alpha
    and numeric characters are processed.
    """
    value = sanitize_text_for_postgres(text or "")
    if not value:
        return ""

    def _replace(m: re.Match) -> str:
        token = m.group(0)
        if not _is_likely_identifier(token):
            return token
        return _normalize_identifier_token(token)

    return _ID_CANDIDATE_RE.sub(_replace, value)
