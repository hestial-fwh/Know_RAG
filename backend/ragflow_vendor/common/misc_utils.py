"""Minimal misc utilities required by RAGFlow DeepDoc vendor code."""
from __future__ import annotations

import asyncio
import functools
import hashlib
import os
import threading
from concurrent.futures import ThreadPoolExecutor
from functools import wraps
from typing import Any, Callable

_EXECUTOR: ThreadPoolExecutor | None = None


def _thread_pool_executor() -> ThreadPoolExecutor:
    global _EXECUTOR
    if _EXECUTOR is None:
        try:
            max_workers = int(os.getenv("THREAD_POOL_MAX_WORKERS", "128"))
        except Exception:
            max_workers = 128
        _EXECUTOR = ThreadPoolExecutor(max_workers=max(1, max_workers))
    return _EXECUTOR


async def thread_pool_exec(func: Callable, *args: Any, **kwargs: Any) -> Any:
    """RAGFlow-compatible async thread-pool executor."""
    loop = asyncio.get_running_loop()
    if kwargs:
        bound = functools.partial(func, *args, **kwargs)
        return await loop.run_in_executor(_thread_pool_executor(), bound)
    return await loop.run_in_executor(_thread_pool_executor(), func, *args)


def pip_install_torch() -> bool:
    """know_rag does not auto-install torch inside the running container."""
    return False


def hash_str2int(text: Any, mod: int = 10**8) -> int:
    return int(hashlib.md5(str(text).encode()).hexdigest(), 16) % mod


def once(func: Callable) -> Callable:
    cache = {}
    @wraps(func)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        if "value" not in cache:
            cache["value"] = func(*args, **kwargs)
        return cache["value"]
    return wrapper


def run_async_compat(coro):
    """Run an async coroutine from both sync code and an already-running event loop.

    RAGFlow DeepDoc calls asyncio.run() inside synchronous parser code. In know_rag
    that parser may be invoked from FastAPI's running event loop, where asyncio.run()
    raises: "asyncio.run() cannot be called from a running event loop".
    When a loop is already running, execute the coroutine in a short-lived helper
    thread with its own event loop and propagate the result/exception.
    """
    try:
        loop = asyncio.get_running_loop()
        running = loop.is_running()
    except RuntimeError:
        running = False

    if not running:
        return asyncio.run(coro)

    result_box = {}

    def _runner():
        try:
            result_box["result"] = asyncio.run(coro)
        except BaseException as exc:  # propagate original parser errors
            result_box["error"] = exc

    t = threading.Thread(target=_runner, name="ragflow-asyncio-compat", daemon=True)
    t.start()
    t.join()
    if "error" in result_box:
        raise result_box["error"]
    return result_box.get("result")
