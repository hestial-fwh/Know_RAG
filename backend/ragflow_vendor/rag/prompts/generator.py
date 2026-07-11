"""Prompt helpers compatible with RAGFlow parser-side imports."""
from __future__ import annotations


def vision_llm_describe_prompt(page=None):
    prefix = f"Page {page}: " if page is not None else ""
    return prefix + "Describe this document image/table in detail and extract all visible text and data."


def vision_llm_figure_describe_prompt():
    return (
        "You are analyzing a figure/table extracted from a document. "
        "Describe the visual content, important labels, axes, legends, visible text, and key conclusions."
    )


def vision_llm_figure_describe_prompt_with_context(context: str | None = None):
    ctx = (context or "").strip()
    if ctx:
        return (
            "You are analyzing a figure/table extracted from a document. "
            "Use the surrounding document context when helpful.\n\n"
            f"Context:\n{ctx}\n\n"
            "Describe the visual content, important labels, visible text, and key conclusions."
        )
    return vision_llm_figure_describe_prompt()
