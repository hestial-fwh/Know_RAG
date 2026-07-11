"""Minimal RAGFlow config compatibility layer."""
from __future__ import annotations

import os
from typing import Any


def get_base_config(key: str | None = None, default: Any = None) -> Any:
    if not key:
        return default
    return os.getenv(str(key), default)


def get_project_base_directory(*args):
    from common.file_utils import get_project_base_directory as _base
    return _base(*args)
