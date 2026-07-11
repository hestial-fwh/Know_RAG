"""LLM service stub for parser-only RAGFlow figure parser imports."""
from __future__ import annotations

from typing import Any


class LLMBundle:
    def __init__(self, *args: Any, **kwargs: Any):
        self.args = args
        self.kwargs = kwargs

    def describe(self, *args: Any, **kwargs: Any) -> str:
        return ""

    def chat(self, *args: Any, **kwargs: Any) -> str:
        return ""
