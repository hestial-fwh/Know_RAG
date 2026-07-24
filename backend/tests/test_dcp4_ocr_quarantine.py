"""DCP-4: OCR shadow + index admission tests.

Tests the OCR quarantine pipeline: feature flag gating, embedding content
stripping for visual-collection pages, and preservation for scanned-prose
and poster-page OCR text.
"""

from unittest.mock import MagicMock

from app.services.content_cleaning import (
    _TRUSTED_SKIP_REASONS,
    OCR_IMAGE_MODE_NORMAL,
    OCR_IMAGE_MODE_SHADOW_AUXILIARY,
    METADATA_KEY_IMAGE_OCR_MODE,
    SKIP_REASON_VISUAL_OCR_QUARANTINE,
    is_visual_ocr_quarantined,
    should_skip_text_chunk,
)
from app.services.embedding.embedding_content_builder import build_embedding_content
from app.services.embedding_service import _apply_visual_ocr_quarantine


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_chunk(chunk_type, metadata=None, image_info=None, content=None, flags=None):
    """Build a minimal mock chunk for build_embedding_content."""
    chunk = MagicMock()
    chunk.chunk_type = chunk_type
    chunk.metadata_json = metadata or {}
    chunk.image_info = image_info or {}
    chunk.content = content or ""
    chunk.flags = flags or {}
    chunk.context_header = ""
    return chunk


# ---------------------------------------------------------------------------
# is_visual_ocr_quarantined helper
# ---------------------------------------------------------------------------


def test_is_visual_ocr_quarantined_false_when_flag_off():
    """Flag OFF → never quarantined, regardless of metadata."""
    meta = {METADATA_KEY_IMAGE_OCR_MODE: OCR_IMAGE_MODE_SHADOW_AUXILIARY}
    assert is_visual_ocr_quarantined(meta, enabled=False) is False


def test_is_visual_ocr_quarantined_false_when_normal_mode():
    """Flag ON + normal mode → not quarantined."""
    meta = {METADATA_KEY_IMAGE_OCR_MODE: OCR_IMAGE_MODE_NORMAL}
    assert is_visual_ocr_quarantined(meta, enabled=True) is False


def test_is_visual_ocr_quarantined_false_when_no_metadata():
    """Flag ON + no image_ocr_mode key → not quarantined."""
    assert is_visual_ocr_quarantined({}, enabled=True) is False
    assert is_visual_ocr_quarantined(None, enabled=True) is False


def test_is_visual_ocr_quarantined_true_when_shadow_auxiliary_and_flag_on():
    """Flag ON + shadow_auxiliary → quarantined."""
    meta = {METADATA_KEY_IMAGE_OCR_MODE: OCR_IMAGE_MODE_SHADOW_AUXILIARY}
    assert is_visual_ocr_quarantined(meta, enabled=True) is True


def test_is_visual_ocr_quarantined_reads_settings_default():
    """When enabled is None, reads from settings (default False)."""
    meta = {METADATA_KEY_IMAGE_OCR_MODE: OCR_IMAGE_MODE_SHADOW_AUXILIARY}
    assert is_visual_ocr_quarantined(meta) is False


# ---------------------------------------------------------------------------
# Trusted skip reason
# ---------------------------------------------------------------------------


def test_visual_ocr_quarantine_in_trusted_skip_reasons():
    """'visual_ocr_quarantine' is a trusted skip reason."""
    assert SKIP_REASON_VISUAL_OCR_QUARANTINE in _TRUSTED_SKIP_REASONS


def test_should_skip_respects_visual_ocr_quarantine():
    """should_skip_text_chunk returns True for trusted visual_ocr_quarantine reason."""
    result = should_skip_text_chunk(
        "some text",
        chunk_type="text",
        flags={"skip_embedding_recommended": True, "skip_reason": SKIP_REASON_VISUAL_OCR_QUARANTINE},
        metadata={"skip_embedding_recommended": True, "skip_reason": SKIP_REASON_VISUAL_OCR_QUARANTINE},
    )
    assert result is True


# ---------------------------------------------------------------------------
# build_embedding_content — image chunks (flag OFF → default behavior)
# ---------------------------------------------------------------------------


def test_image_chunk_ocr_preserved_when_shadow_auxiliary_flag_off():
    """Flag OFF (default): image chunk OCR text is included even with shadow_auxiliary metadata."""
    chunk = _make_chunk(
        chunk_type="image",
        content="图片",
        metadata={
            METADATA_KEY_IMAGE_OCR_MODE: OCR_IMAGE_MODE_SHADOW_AUXILIARY,
            "caption": "书法作品",
            "vlm_description": "一幅行书作品",
            "ocr_text": "题跋文字内容",
        },
    )
    content = build_embedding_content(chunk, None)
    assert "书法作品" in content
    assert "行书作品" in content
    # Flag is off by default → OCR text should be present
    assert "题跋文字内容" in content


def test_image_chunk_ocr_stripped_when_quarantined_flag_on(monkeypatch):
    """Flag ON + shadow_auxiliary: image chunk OCR stripped, VLM desc + caption kept."""
    monkeypatch.setattr(
        "app.services.content_cleaning.settings.document_visual_ocr_quarantine_enabled",
        True,
    )
    monkeypatch.setattr(
        "app.services.embedding.embedding_content_builder.settings.document_visual_ocr_quarantine_enabled",
        True,
    )
    chunk = _make_chunk(
        chunk_type="image",
        content="图片",
        metadata={
            METADATA_KEY_IMAGE_OCR_MODE: OCR_IMAGE_MODE_SHADOW_AUXILIARY,
            "caption": "书法作品",
            "vlm_description": "一幅行书作品",
            "ocr_text": "题跋文字内容",
        },
    )
    content = build_embedding_content(chunk, None)
    assert "书法作品" in content
    assert "行书作品" in content
    # OCR text must be stripped
    assert "题跋文字内容" not in content


def test_image_chunk_scanned_prose_ocr_preserved_with_flag_on(monkeypatch):
    """Flag ON + scanned-prose (image_ocr_mode='normal') → OCR preserved."""
    monkeypatch.setattr(
        "app.services.content_cleaning.settings.document_visual_ocr_quarantine_enabled",
        True,
    )
    monkeypatch.setattr(
        "app.services.embedding.embedding_content_builder.settings.document_visual_ocr_quarantine_enabled",
        True,
    )
    chunk = _make_chunk(
        chunk_type="image",
        content="图片",
        metadata={
            METADATA_KEY_IMAGE_OCR_MODE: OCR_IMAGE_MODE_NORMAL,
            "caption": "合同扫描件",
            "ocr_text": "甲方应于2024年1月1日前支付",
        },
    )
    content = build_embedding_content(chunk, None)
    assert "甲方应于2024年1月1日前支付" in content


def test_image_chunk_poster_ocr_preserved_with_flag_on(monkeypatch):
    """Flag ON + poster-page (image_ocr_mode='normal') → OCR preserved."""
    monkeypatch.setattr(
        "app.services.content_cleaning.settings.document_visual_ocr_quarantine_enabled",
        True,
    )
    monkeypatch.setattr(
        "app.services.embedding.embedding_content_builder.settings.document_visual_ocr_quarantine_enabled",
        True,
    )
    chunk = _make_chunk(
        chunk_type="image",
        content="图片",
        metadata={
            METADATA_KEY_IMAGE_OCR_MODE: OCR_IMAGE_MODE_NORMAL,
            "caption": "活动海报",
            "ocr_text": "年度技术峰会 2024年6月",
        },
    )
    content = build_embedding_content(chunk, None)
    assert "年度技术峰会 2024年6月" in content


def test_image_chunk_no_ocr_mode_key_unchanged_with_flag_on(monkeypatch):
    """Flag ON + no chunk_policy_image_ocr_mode → OCR preserved (no policy metadata)."""
    monkeypatch.setattr(
        "app.services.content_cleaning.settings.document_visual_ocr_quarantine_enabled",
        True,
    )
    monkeypatch.setattr(
        "app.services.embedding.embedding_content_builder.settings.document_visual_ocr_quarantine_enabled",
        True,
    )
    chunk = _make_chunk(
        chunk_type="image",
        content="图片",
        metadata={"caption": "普通图片", "ocr_text": "一些OCR文字"},
    )
    content = build_embedding_content(chunk, None)
    assert "一些OCR文字" in content


# ---------------------------------------------------------------------------
# build_embedding_content — image_ocr_text chunks
# ---------------------------------------------------------------------------


def test_image_ocr_text_chunk_returns_content_when_flag_off():
    """Flag OFF: image_ocr_text chunk returns content even with shadow_auxiliary."""
    chunk = _make_chunk(
        chunk_type="image_ocr_text",
        content="古籍题跋——永乐十五年仲春",
        metadata={METADATA_KEY_IMAGE_OCR_MODE: OCR_IMAGE_MODE_SHADOW_AUXILIARY},
    )
    content = build_embedding_content(chunk, None)
    assert "古籍题跋" in content


def test_image_ocr_text_chunk_returns_empty_when_quarantined_flag_on(monkeypatch):
    """Flag ON + shadow_auxiliary: image_ocr_text returns empty string."""
    monkeypatch.setattr(
        "app.services.content_cleaning.settings.document_visual_ocr_quarantine_enabled",
        True,
    )
    monkeypatch.setattr(
        "app.services.embedding.embedding_content_builder.settings.document_visual_ocr_quarantine_enabled",
        True,
    )
    chunk = _make_chunk(
        chunk_type="image_ocr_text",
        content="古籍题跋——永乐十五年仲春",
        metadata={METADATA_KEY_IMAGE_OCR_MODE: OCR_IMAGE_MODE_SHADOW_AUXILIARY},
    )
    content = build_embedding_content(chunk, None)
    assert content == ""


def test_image_ocr_text_chunk_normal_mode_returns_content_with_flag_on(monkeypatch):
    """Flag ON + normal mode: image_ocr_text returns content normally."""
    monkeypatch.setattr(
        "app.services.content_cleaning.settings.document_visual_ocr_quarantine_enabled",
        True,
    )
    monkeypatch.setattr(
        "app.services.embedding.embedding_content_builder.settings.document_visual_ocr_quarantine_enabled",
        True,
    )
    chunk = _make_chunk(
        chunk_type="image_ocr_text",
        content="合同条款第一条",
        metadata={METADATA_KEY_IMAGE_OCR_MODE: OCR_IMAGE_MODE_NORMAL},
    )
    content = build_embedding_content(chunk, None)
    assert "合同条款第一条" in content


# ---------------------------------------------------------------------------
# build_embedding_content — parent_text and table chunks
# ---------------------------------------------------------------------------


def test_parent_text_unaffected_by_quarantine(monkeypatch):
    """Parent text chunks always return '' regardless of quarantine metadata."""
    monkeypatch.setattr(
        "app.services.content_cleaning.settings.document_visual_ocr_quarantine_enabled",
        True,
    )
    monkeypatch.setattr(
        "app.services.embedding.embedding_content_builder.settings.document_visual_ocr_quarantine_enabled",
        True,
    )
    chunk = _make_chunk(
        chunk_type="parent_text",
        content="some parent content",
        metadata={METADATA_KEY_IMAGE_OCR_MODE: OCR_IMAGE_MODE_SHADOW_AUXILIARY},
    )
    content = build_embedding_content(chunk, None)
    assert content == ""


def test_table_chunk_ocr_stripped_when_quarantined_flag_on(monkeypatch):
    """Flag ON + shadow_auxiliary: quarantined table chunk returns empty (caption alone too short)."""
    monkeypatch.setattr(
        "app.services.content_cleaning.settings.document_visual_ocr_quarantine_enabled",
        True,
    )
    monkeypatch.setattr(
        "app.services.embedding.embedding_content_builder.settings.document_visual_ocr_quarantine_enabled",
        True,
    )
    chunk = _make_chunk(
        chunk_type="table",
        content="| Name | Value |\n|------|-------|",
        metadata={
            METADATA_KEY_IMAGE_OCR_MODE: OCR_IMAGE_MODE_SHADOW_AUXILIARY,
            "caption": "实验数据表格",
            "ocr_text": "扫描表格中的OCR文字",
        },
        image_info={"table_html": "<table><tr><td>OCR数据</td></tr></table>"},
    )
    content = build_embedding_content(chunk, None)
    # When quarantined, table content, HTML, and OCR are all stripped.
    # The remaining caption alone is too short to pass should_skip_text_chunk,
    # so the result is empty.  In production embedding_service.py hard-skips
    # quarantined chunks before build_embedding_content is even called.
    assert content == ""


# ---------------------------------------------------------------------------
# _apply_visual_ocr_quarantine helper (embedding_service.py)
# ---------------------------------------------------------------------------


def _make_chunk_for_quarantine(metadata=None, flags=None):
    """Build a minimal mock chunk with metadata_json and flags attributes."""
    chunk = MagicMock()
    chunk.metadata_json = metadata or {}
    chunk.flags = flags or {}
    return chunk


def test_apply_visual_ocr_quarantine_returns_true_and_stamps_flags(monkeypatch):
    """Flag ON + shadow_auxiliary → returns True, stamps quarantine keys on both dicts."""
    monkeypatch.setattr(
        "app.services.content_cleaning.settings.document_visual_ocr_quarantine_enabled",
        True,
    )
    monkeypatch.setattr(
        "app.services.embedding_service.settings.document_visual_ocr_quarantine_enabled",
        True,
    )
    chunk = _make_chunk_for_quarantine(
        metadata={METADATA_KEY_IMAGE_OCR_MODE: OCR_IMAGE_MODE_SHADOW_AUXILIARY},
    )

    result = _apply_visual_ocr_quarantine(chunk)

    assert result is True
    assert chunk.metadata_json["skip_embedding_recommended"] is True
    assert chunk.metadata_json["skip_reason"] == SKIP_REASON_VISUAL_OCR_QUARANTINE
    assert chunk.flags["skip_embedding_recommended"] is True
    assert chunk.flags["skip_reason"] == SKIP_REASON_VISUAL_OCR_QUARANTINE


def test_apply_visual_ocr_quarantine_returns_false_when_not_quarantined(monkeypatch):
    """Flag ON + normal mode → returns False, chunk unchanged."""
    monkeypatch.setattr(
        "app.services.content_cleaning.settings.document_visual_ocr_quarantine_enabled",
        True,
    )
    monkeypatch.setattr(
        "app.services.embedding_service.settings.document_visual_ocr_quarantine_enabled",
        True,
    )
    original_meta = {METADATA_KEY_IMAGE_OCR_MODE: OCR_IMAGE_MODE_NORMAL}
    original_flags = {}
    chunk = _make_chunk_for_quarantine(metadata=original_meta, flags=original_flags)

    result = _apply_visual_ocr_quarantine(chunk)

    assert result is False
    # Chunk should be untouched
    assert chunk.metadata_json == original_meta
    assert chunk.flags == original_flags


def test_apply_visual_ocr_quarantine_flag_off_returns_false(monkeypatch):
    """Flag OFF → returns False even with shadow_auxiliary metadata."""
    monkeypatch.setattr(
        "app.services.content_cleaning.settings.document_visual_ocr_quarantine_enabled",
        False,
    )
    monkeypatch.setattr(
        "app.services.embedding_service.settings.document_visual_ocr_quarantine_enabled",
        False,
    )
    chunk = _make_chunk_for_quarantine(
        metadata={METADATA_KEY_IMAGE_OCR_MODE: OCR_IMAGE_MODE_SHADOW_AUXILIARY},
    )

    result = _apply_visual_ocr_quarantine(chunk)

    assert result is False


def test_apply_visual_ocr_quarantine_idempotent_no_dirty_write(monkeypatch):
    """When quarantine keys already present, returns True without mutating."""
    monkeypatch.setattr(
        "app.services.content_cleaning.settings.document_visual_ocr_quarantine_enabled",
        True,
    )
    monkeypatch.setattr(
        "app.services.embedding_service.settings.document_visual_ocr_quarantine_enabled",
        True,
    )
    already_stamped_meta = {
        METADATA_KEY_IMAGE_OCR_MODE: OCR_IMAGE_MODE_SHADOW_AUXILIARY,
        "skip_embedding_recommended": True,
        "skip_reason": SKIP_REASON_VISUAL_OCR_QUARANTINE,
    }
    already_stamped_flags = {
        "skip_embedding_recommended": True,
        "skip_reason": SKIP_REASON_VISUAL_OCR_QUARANTINE,
    }
    chunk = _make_chunk_for_quarantine(
        metadata=dict(already_stamped_meta),
        flags=dict(already_stamped_flags),
    )

    result = _apply_visual_ocr_quarantine(chunk)

    assert result is True
    # Content unchanged — early return prevented dict copy + reassign.
    assert chunk.metadata_json == already_stamped_meta
    assert chunk.flags == already_stamped_flags
    # Keys not duplicated.
    assert len([k for k in chunk.metadata_json if k == "skip_reason"]) == 1
