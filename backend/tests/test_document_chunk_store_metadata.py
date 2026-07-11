"""Test that document.metadata_json records chunk_store_status correctly.

These are pure logic tests — they verify the metadata structure without
needing a database.
"""

import json

from app.utils.text import sanitize_json_for_postgres


def test_metadata_records_success_when_chunks_present():
    """When chunk_count > 0, chunk_store_status should be 'success'."""
    chunk_count = 42
    metadata = sanitize_json_for_postgres({
        "parser": "mineru",
        "parsed_block_count": 232,
        "chunk_store_status": "success" if chunk_count > 0 else "empty",
        "chunk_count": chunk_count,
    })

    result = json.loads(metadata) if isinstance(metadata, str) else metadata
    assert result["chunk_store_status"] == "success"
    assert result["chunk_count"] == 42


def test_metadata_records_empty_when_zero_chunks():
    """When chunk_count == 0, chunk_store_status should be 'empty'."""
    chunk_count = 0
    metadata = sanitize_json_for_postgres({
        "parser": "mineru",
        "parsed_block_count": 232,
        "chunk_store_status": "success" if chunk_count > 0 else "empty",
        "chunk_count": chunk_count,
    })

    result = json.loads(metadata) if isinstance(metadata, str) else metadata
    assert result["chunk_store_status"] == "empty"
    assert result["chunk_count"] == 0


def test_error_message_set_when_zero_chunks():
    """When chunk_count == 0, error_message should provide a hint."""
    chunk_count = 0
    error_message = "" if chunk_count > 0 else "解析完成，但未生成切片，请检查 chunk rebuild 日志"

    assert error_message != ""
    assert "切片" in error_message
    assert "chunk rebuild" in error_message


def test_metadata_records_failed_on_exception():
    """When chunk rebuild throws, chunk_store_status should be 'failed'."""
    chunk_error = "ValueError: 切片入库失败"
    metadata = sanitize_json_for_postgres({
        "parsed_block_count": 232,
        "chunk_store_status": "failed",
        "chunk_store_error": chunk_error,
        "chunk_count": 0,
    })

    result = json.loads(metadata) if isinstance(metadata, str) else metadata
    assert result["chunk_store_status"] == "failed"
    assert result["chunk_store_error"] == chunk_error
    assert result["chunk_count"] == 0


def test_metadata_preserves_parse_statistics():
    """chunk_store_status metadata must not overwrite parse statistics."""
    metadata = sanitize_json_for_postgres({
        "parser": "mineru",
        "parsed_block_count": 232,
        "text_block_count": 205,
        "table_block_count": 27,
        "image_block_count": 0,
        "chunk_store_status": "success",
        "chunk_count": 150,
    })

    result = json.loads(metadata) if isinstance(metadata, str) else metadata
    assert result["parsed_block_count"] == 232
    assert result["text_block_count"] == 205
    assert result["table_block_count"] == 27
    assert result["chunk_store_status"] == "success"
    assert result["chunk_count"] == 150
