"""Minimal constants required by vendored RAGFlow parser modules."""
from __future__ import annotations

from enum import Enum

MAXIMUM_PAGE_NUMBER = 100000
TAG_FLD = "tag_kwd"
RAG_FLOW_SERVICE_NAME = "know-rag-ragflow-vendor"


class LLMType(str, Enum):
    CHAT = "chat"
    EMBEDDING = "embedding"
    IMAGE2TEXT = "image2text"
    SPEECH2TEXT = "speech2text"
    RERANK = "rerank"
    TTS = "tts"
