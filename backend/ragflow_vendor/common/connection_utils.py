"""Small timeout helper compatible with RAGFlow parser imports."""
from __future__ import annotations

import contextlib
from functools import wraps
from typing import Callable, Iterator


@contextlib.contextmanager
def _timeout_context(_seconds: int | float | None = None) -> Iterator[None]:
    yield


def timeout(seconds: int | float | None = None):
    """Return a no-op context manager/decorator.

    RAGFlow uses this for optional VLM/image calls.  know_rag controls request
    timeouts in its own services, so parser-side timeout is intentionally no-op.
    """
    def decorator(fn: Callable):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            return fn(*args, **kwargs)
        return wrapper

    # Supports both `with timeout(10):` and `@timeout(10)` style.
    class _Timeout:
        def __enter__(self):
            return None
        def __exit__(self, exc_type, exc, tb):
            return False
        def __call__(self, fn: Callable):
            return decorator(fn)

    return _Timeout()
