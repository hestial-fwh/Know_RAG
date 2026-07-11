"""Merge orphan short text blocks into adjacent code blocks before chunking.

Extracted from chunk_service.py — pure logic operating on ParsedBlock objects.
No database dependency.
"""

from __future__ import annotations

import re
from typing import Any

from app.services.chunker.markdown_splitter import is_orphan_short_text_chunk
from app.services.content_cleaning import clean_chunk_content_for_storage, clear_embedding_skip_metadata
from app.utils.text import sanitize_json_for_postgres, sanitize_text_for_postgres

# ---------------------------------------------------------------------------
# Constants (migrated from chunk_service.py)
# ---------------------------------------------------------------------------

CODE_BLOCK_RAW_TYPES = {"code", "code_block", "fenced_code", "pre", "source_code"}
CODE_LEAD_IN_SUFFIXES = ("：", ":", "如下", "如下：", "主要入口：", "示例：", "流程：", "管道：", "实现：")
CODE_TAIL_NOTE_RE = re.compile(r"^\s*(返回|输出|完成后|用于|说明|结果|写入|生成|得到)\b", re.I)


# ---------------------------------------------------------------------------
# Code block detection
# ---------------------------------------------------------------------------


def _raw_block_type(block: Any) -> str:
    meta = getattr(block, "metadata", None) or {}
    for key in ("raw_block_type", "type", "block_type", "semantic_type", "subtype"):
        value = meta.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip().lower()
    return (getattr(block, "block_type", None) or "text").strip().lower()


def is_fenced_code_text(text: str) -> bool:
    return bool(re.match(r"^\s*```[\s\S]*?```\s*$", sanitize_text_for_postgres(text or "").strip()))


def is_code_block_block(block: Any) -> bool:
    rtype = _raw_block_type(block)
    meta = getattr(block, "metadata", None) or {}
    return (
        rtype in CODE_BLOCK_RAW_TYPES
        or meta.get("content_kind") == "code_block"
        or is_fenced_code_text(getattr(block, "text", ""))
    )


def looks_like_code_lead_in(text: str) -> bool:
    value = sanitize_text_for_postgres(text or "").strip()
    return bool(
        value
        and (
            value.endswith(CODE_LEAD_IN_SUFFIXES)
            or re.search(r"(主要入口|示例|流程|管道|实现)\s*[:：]?$", value)
        )
    )


def looks_like_code_tail_note(text: str) -> bool:
    value = sanitize_text_for_postgres(text or "").strip()
    return bool(
        value
        and (
            CODE_TAIL_NOTE_RE.search(value)
            or re.search(r"(数据库|后续检索|返回值|输出)\s*[。.!?]?$", value)
        )
    )


def is_orphan_short_text(content: str, metadata: dict | None = None) -> bool:
    return is_orphan_short_text_chunk(content, metadata)


# ---------------------------------------------------------------------------
# Merge logic
# ---------------------------------------------------------------------------


def _append_block_meta_text(block: Any, key: str, text: str) -> None:
    value = sanitize_text_for_postgres(text or "").strip()
    if not value:
        return
    meta = dict(getattr(block, "metadata", None) or {})
    existing = sanitize_text_for_postgres(meta.get(key) or "").strip()
    meta[key] = "\n".join(part for part in [existing, value] if part)
    meta["content_kind"] = "code_block"
    meta["merged_short_text_context"] = True
    if key == "lead_in_text":
        meta["has_lead_in"] = True
    if key == "tail_note_text":
        meta["has_tail_note"] = True
    block.metadata = sanitize_json_for_postgres(meta)


def merge_short_text_around_code_blocks(blocks: list[Any]) -> list[Any]:
    """Merge orphan short text blocks into adjacent code blocks before chunking."""
    if not blocks:
        return []

    result: list[Any] = []
    index = 0
    while index < len(blocks):
        block = blocks[index]

        # Only process short ordinary text blocks
        if (
            getattr(block, "block_type", "") != "text"
            or is_code_block_block(block)
            or not is_orphan_short_text(
                getattr(block, "text", ""), getattr(block, "metadata", None)
            )
        ):
            result.append(block)
            index += 1
            continue

        prev_block = result[-1] if result else None
        next_block = blocks[index + 1] if index + 1 < len(blocks) else None
        prev_is_code = prev_block is not None and is_code_block_block(prev_block)
        next_is_code = next_block is not None and is_code_block_block(next_block)
        text = sanitize_text_for_postgres(getattr(block, "text", "")).strip()

        if next_is_code and (
            looks_like_code_lead_in(text)
            or not prev_is_code
            or not looks_like_code_tail_note(text)
        ):
            _append_block_meta_text(next_block, "lead_in_text", text)
            next_meta = clear_embedding_skip_metadata(getattr(next_block, "metadata", None) or {})
            next_meta["merged_into_code_block"] = True
            next_block.metadata = sanitize_json_for_postgres(next_meta)
            index += 1
            continue

        if prev_is_code:
            _append_block_meta_text(prev_block, "tail_note_text", text)
            prev_meta = clear_embedding_skip_metadata(getattr(prev_block, "metadata", None) or {})
            prev_meta["merged_into_code_block"] = True
            prev_block.metadata = sanitize_json_for_postgres(prev_meta)
            index += 1
            continue

        if (
            prev_block is not None
            and getattr(prev_block, "block_type", "") == "text"
            and not is_code_block_block(prev_block)
            and (getattr(prev_block, "context_header", "") or "")
            == (getattr(block, "context_header", "") or "")
        ):
            prev_block.text = sanitize_text_for_postgres(
                f"{getattr(prev_block, 'text', '')}\n{text}"
            )
            prev_meta = clear_embedding_skip_metadata(getattr(prev_block, "metadata", None) or {})
            prev_meta["absorbed_orphan_short_text"] = True
            prev_block.metadata = sanitize_json_for_postgres(prev_meta)
            index += 1
            continue

        if (
            next_block is not None
            and getattr(next_block, "block_type", "") == "text"
            and not is_code_block_block(next_block)
            and (getattr(next_block, "context_header", "") or "")
            == (getattr(block, "context_header", "") or "")
        ):
            next_block.text = sanitize_text_for_postgres(
                f"{text}\n{getattr(next_block, 'text', '')}"
            )
            next_meta = clear_embedding_skip_metadata(getattr(next_block, "metadata", None) or {})
            next_meta["absorbed_orphan_short_text"] = True
            next_block.metadata = sanitize_json_for_postgres(next_meta)
            index += 1
            continue

        block_meta = clear_embedding_skip_metadata(getattr(block, "metadata", None) or {})
        block_meta.update({
            "low_information": True,
            "orphan_short_chunk": True,
            "skip_embedding_recommended": True,
            "skip_reason": "orphan_short_chunk",
        })
        block.metadata = sanitize_json_for_postgres(block_meta)
        result.append(block)
        index += 1

    # Restore order indices
    for order, item in enumerate(result):
        if hasattr(item, "order_index"):
            item.order_index = order
    return result


def compose_code_block_content(block: Any) -> str:
    """Build code child content with absorbed lead-in/tail-note context."""
    meta = getattr(block, "metadata", None) or {}
    lead = sanitize_text_for_postgres(meta.get("lead_in_text") or "")
    tail = sanitize_text_for_postgres(meta.get("tail_note_text") or "")
    code = sanitize_text_for_postgres(
        getattr(block, "text", "") or (getattr(block, "index_text", lambda: "")() or "")
    )
    return clean_chunk_content_for_storage(
        "\n\n".join(part for part in [lead, code, tail] if part), chunk_type="text"
    )
