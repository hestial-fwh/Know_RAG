"""Minimal image-to-text stub used by vendored RAGFlow figure_parser.

know_rag performs VLM enrichment in app.services.vision_service after parsing;
this stub prevents RAGFlow parser imports from pulling the whole RAGFlow app
stack into know_rag.
"""
from __future__ import annotations

from typing import Any


def vision_llm_chunk(*args: Any, **kwargs: Any) -> str:
    return ""
