"""Login captcha — SVG generation + Redis / in-memory storage."""

from __future__ import annotations

import base64
import logging
import math
import random
import string
import time
from io import BytesIO
from typing import Optional

import redis

from app.core.config import settings

logger = logging.getLogger(__name__)

# ── character set (case-insensitive — uppercase + digits, no ambiguous chars) ──
_CAPTCHA_CHARS = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
_CAPTCHA_LENGTH = 4

# ── in-memory fallback store ──────────────────────────────────────────────────
_memory_store: dict[str, tuple[str, float]] = {}  # captcha_id -> (code, expires_at)


def _redis_client() -> Optional[redis.Redis]:
    """Return a Redis client, or None when Redis is unreachable."""
    try:
        r = redis.Redis.from_url(settings.redis_url, socket_connect_timeout=2, decode_responses=True)
        r.ping()
        return r
    except Exception:
        logger.warning("captcha: Redis unavailable, using in-memory fallback (dev only)")
        return None


def _generate_code() -> str:
    return "".join(random.choices(_CAPTCHA_CHARS, k=_CAPTCHA_LENGTH))


def _store_captcha(captcha_id: str, code: str, ttl: int) -> None:
    r = _redis_client()
    if r is not None:
        try:
            r.setex(f"login_captcha:{captcha_id}", ttl, code)
            return
        except Exception:
            logger.warning("captcha: Redis write failed, falling back to memory")
    _memory_store[captcha_id] = (code, time.time() + ttl)


def _get_and_delete_captcha(captcha_id: str) -> Optional[str]:
    r = _redis_client()
    if r is not None:
        try:
            code = r.get(f"login_captcha:{captcha_id}")
            if code is not None:
                r.delete(f"login_captcha:{captcha_id}")
                return code
            return None
        except Exception:
            logger.warning("captcha: Redis read failed, falling back to memory")
    entry = _memory_store.pop(captcha_id, None)
    if entry is None:
        return None
    code, expires_at = entry
    if time.time() > expires_at:
        return None
    return code


# ── SVG generation ────────────────────────────────────────────────────────────

def _svg_captcha(code: str) -> str:
    """Build an inline SVG with noise lines, dots, and rotated characters."""
    width, height = 140, 52
    # Use BytesIO as a string builder for the SVG
    buf = BytesIO()

    def w(s: str) -> None:
        buf.write(s.encode("utf-8"))

    w(f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">')

    # background
    bg_color = f"hsl({random.randint(200, 260)}, 20%, {random.randint(92, 97)}%)"
    w(f'<rect width="{width}" height="{height}" fill="{bg_color}" rx="4"/>')

    # noise dots
    for _ in range(random.randint(30, 50)):
        cx = random.randint(2, width - 2)
        cy = random.randint(2, height - 2)
        r = random.uniform(0.5, 1.5)
        opacity = random.uniform(0.2, 0.6)
        color = f"hsl({random.randint(0, 360)}, 30%, {random.randint(30, 70)}%)"
        w(f'<circle cx="{cx}" cy="{cy}" r="{r}" fill="{color}" opacity="{opacity}"/>')

    # interference lines
    for _ in range(random.randint(2, 4)):
        x1 = random.randint(0, width // 3)
        y1 = random.randint(0, height)
        x2 = random.randint(2 * width // 3, width)
        y2 = random.randint(0, height)
        stroke = f"hsl({random.randint(0, 360)}, 40%, {random.randint(40, 70)}%)"
        opacity = random.uniform(0.25, 0.55)
        w(f'<line x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}" stroke="{stroke}" stroke-width="{random.uniform(0.8, 1.6)}" opacity="{opacity}"/>')

    # characters
    char_width = width / (_CAPTCHA_LENGTH + 1)
    for i, ch in enumerate(code):
        cx = char_width * (i + 0.75)
        cy = height / 2 + random.randint(-4, 8)
        angle = random.randint(-25, 25)
        font_size = random.randint(24, 32)
        color = f"hsl({random.randint(0, 360)}, 50%, {random.randint(25, 45)}%)"
        ff = random.choice(["Arial, sans-serif", "Georgia, serif", "Courier New, monospace", "Impact, sans-serif"])
        transform = f"rotate({angle}, {cx}, {cy})"
        w(f'<text x="{cx}" y="{cy}" font-size="{font_size}" font-family="{ff}" font-weight="bold" fill="{color}" text-anchor="middle" dominant-baseline="central" transform="{transform}">{ch}</text>')

    w("</svg>")
    return buf.getvalue().decode("utf-8")


# ── public API ────────────────────────────────────────────────────────────────

def generate_captcha() -> dict:
    """Create a captcha, return {captcha_id, image (base64 data-uri)}."""
    code = _generate_code()
    captcha_id = _new_captcha_id()
    ttl = max(30, settings.login_captcha_ttl_seconds)
    _store_captcha(captcha_id, code, ttl)
    svg = _svg_captcha(code)
    b64 = base64.b64encode(svg.encode("utf-8")).decode("ascii")
    return {
        "captcha_id": captcha_id,
        "image": f"data:image/svg+xml;base64,{b64}",
    }


def verify_captcha(captcha_id: str, captcha_code: str) -> bool:
    """Check captcha.  Consumes the code on success.  Case-insensitive."""
    if not settings.login_captcha_enabled:
        return True
    if not captcha_id or not captcha_code:
        return False
    stored = _get_and_delete_captcha(captcha_id)
    if stored is None:
        return False
    return stored.upper() == captcha_code.upper().strip()


def _new_captcha_id() -> str:
    return base64.urlsafe_b64encode(random.randbytes(18)).decode("ascii").rstrip("=")
