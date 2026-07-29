"""DCP-4R field-level content admission regression tests."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.db.models import Chunk
from app.services.content_cleaning import (
    METADATA_KEY_IMAGE_OCR_MODE,
    OCR_IMAGE_MODE_NORMAL,
    OCR_IMAGE_MODE_SHADOW_AUXILIARY,
    SKIP_REASON_VISUAL_OCR_QUARANTINE,
    clear_legacy_visual_ocr_skip_flags,
    should_skip_text_chunk,
)
from app.services.embedding.embedding_content_builder import (
    CONTENT_ADMISSION_VERSION,
    build_content_admission,
    build_embedding_content,
)
from app.services.embedding_service import (
    EmbeddingRuntime,
    _apply_content_admission,
    _revoke_chunk_embedding,
    embed_chunks,
)
from app.services.keyword_index_service import (
    _is_indexable_chunk,
    chunk_to_es_doc,
)


def _chunk(
    chunk_type: str,
    content: str,
    *,
    metadata: dict | None = None,
    flags: dict | None = None,
    image_info: dict | None = None,
):
    return SimpleNamespace(
        id="chunk-1",
        document_id="doc-1",
        knowledge_id="doc-1",
        knowledge_base_id="kb-1",
        chunk_index=0,
        chunk_type=chunk_type,
        content=content,
        context_header="",
        metadata_json=dict(metadata or {}),
        flags=dict(flags or {}),
        image_info=dict(image_info or {}),
        parent_chunk_id=None,
        status="ready",
        is_enabled=True,
    )


def _document():
    return SimpleNamespace(
        id="doc-1",
        knowledge_base_id="kb-1",
        title="书法图录",
        file_name="calligraphy.pdf",
    )


def _enable_field_admission(monkeypatch):
    monkeypatch.setattr(
        "app.services.content_cleaning.settings."
        "document_field_content_admission_enabled",
        True,
    )
    monkeypatch.setattr(
        "app.services.content_cleaning.settings."
        "document_visual_ocr_quarantine_enabled",
        False,
    )


def test_visual_image_keeps_caption_and_vlm_but_shadows_only_ocr(monkeypatch):
    _enable_field_admission(monkeypatch)
    chunk = _chunk(
        "image",
        "图片",
        metadata={
            METADATA_KEY_IMAGE_OCR_MODE: OCR_IMAGE_MODE_SHADOW_AUXILIARY,
            "caption": "王羲之兰亭序",
            "vlm_description": "一幅纵向行书作品，墨色层次清晰",
            "ocr_text": "錯亂草書識別文字",
        },
    )

    result = build_content_admission(chunk, _document())

    assert result.content
    assert "王羲之兰亭序" in result.content
    assert "纵向行书作品" in result.content
    assert "錯亂草書識別文字" not in result.content
    assert result.index_sources == ("caption", "vlm_description")
    assert result.shadow_sources == ("visual_ocr",)


def test_visual_image_unknown_semantic_content_is_not_assumed_to_be_ocr(monkeypatch):
    _enable_field_admission(monkeypatch)
    chunk = _chunk(
        "image",
        "画面中央为行书长卷，左下角有红色印章",
        metadata={
            METADATA_KEY_IMAGE_OCR_MODE: OCR_IMAGE_MODE_SHADOW_AUXILIARY,
            "ocr_text": "錯亂OCR",
        },
    )

    result = build_content_admission(chunk, _document())

    assert "行书长卷" in result.content
    assert "錯亂OCR" not in result.content
    assert "image_content" in result.index_sources


def test_canonical_image_composite_keeps_context_but_shadows_labeled_ocr(
    monkeypatch,
):
    _enable_field_admission(monkeypatch)
    chunk = _chunk(
        "image",
        "\n\n".join(
            [
                "章节：第二章 王羲之书法",
                "图片标题：兰亭序局部",
                "图片脚注：东晋永和九年",
                "上下文：该作品用笔流畅，具有重要研究价值。",
                "图片描述：纵向行书作品，带有红色印章。",
                "图片文字：歲在癸丑暮春之初錯誤字形",
            ]
        ),
        metadata={
            METADATA_KEY_IMAGE_OCR_MODE: OCR_IMAGE_MODE_SHADOW_AUXILIARY,
            "caption": "兰亭序局部",
            "vlm_description": "纵向行书作品，带有红色印章。",
            "ocr_text": "歲在癸丑暮春之初錯誤字形",
        },
    )

    result = build_content_admission(chunk, _document())

    assert "第二章 王羲之书法" in result.content
    assert "东晋永和九年" in result.content
    assert "该作品用笔流畅" in result.content
    assert "纵向行书作品" in result.content
    assert "歲在癸丑暮春之初錯誤字形" not in result.content
    assert "related_text" in result.index_sources
    assert result.shadow_sources == ("visual_ocr",)


def test_canonical_image_without_caption_does_not_leak_labeled_shadow_ocr(
    monkeypatch,
):
    _enable_field_admission(monkeypatch)
    chunk = _chunk(
        "image",
        "上下文：作品位于展览目录第十页。\n\n图片文字：草书错误 OCR",
        metadata={METADATA_KEY_IMAGE_OCR_MODE: OCR_IMAGE_MODE_SHADOW_AUXILIARY},
    )

    result = build_content_admission(chunk, _document())

    assert "作品位于展览目录第十页" in result.content
    assert "草书错误 OCR" not in result.content
    assert result.index_sources == ("related_text",)
    assert result.shadow_sources == ("visual_ocr",)


@pytest.mark.parametrize("origin", ["native_text", "", "parser_text"])
def test_visual_page_reliable_text_chunks_are_never_quarantined(monkeypatch, origin):
    _enable_field_admission(monkeypatch)
    metadata = {
        METADATA_KEY_IMAGE_OCR_MODE: OCR_IMAGE_MODE_SHADOW_AUXILIARY,
        "text_origin": origin,
    }
    chunk = _chunk(
        "text",
        "这是出版社内嵌的艺术家介绍文字，具有可靠的 PDF 文本来源。" * 3,
        metadata=metadata,
    )

    result = build_content_admission(chunk, _document())

    assert "艺术家介绍文字" in result.content
    assert result.shadow_sources == ()
    assert result.index_sources in {("native_text",), ("parser_text",)}


def test_explicit_visual_ocr_child_is_shadowed_without_losing_artifact(monkeypatch):
    _enable_field_admission(monkeypatch)
    chunk = _chunk(
        "image_ocr_text",
        "草书误识别内容仍保存在 Chunk 中",
        metadata={METADATA_KEY_IMAGE_OCR_MODE: OCR_IMAGE_MODE_SHADOW_AUXILIARY},
    )

    result = build_content_admission(chunk, _document())

    assert result.content == ""
    assert result.shadow_sources == ("visual_ocr",)
    assert chunk.content == "草书误识别内容仍保存在 Chunk 中"


def test_explicit_visual_ocr_text_role_is_shadowed(monkeypatch):
    _enable_field_admission(monkeypatch)
    chunk = _chunk(
        "text",
        "被 parser 物化成普通 text 类型的视觉 OCR",
        metadata={
            METADATA_KEY_IMAGE_OCR_MODE: OCR_IMAGE_MODE_SHADOW_AUXILIARY,
            "semantic_role": "visual_ocr_candidate",
        },
    )

    result = build_content_admission(chunk, _document())

    assert result.content == ""
    assert result.shadow_sources == ("visual_ocr",)


@pytest.mark.parametrize("chunk_type", ["image_ocr_text", "image"])
def test_scanned_or_poster_normal_ocr_remains_indexable(monkeypatch, chunk_type):
    _enable_field_admission(monkeypatch)
    chunk = _chunk(
        chunk_type,
        "甲方应于合同签订后十个工作日内支付全部款项。",
        metadata={
            METADATA_KEY_IMAGE_OCR_MODE: OCR_IMAGE_MODE_NORMAL,
            "ocr_text": "甲方应于合同签订后十个工作日内支付全部款项。",
        },
    )

    result = build_content_admission(chunk, _document())

    assert "十个工作日" in result.content
    assert "visual_ocr" in result.index_sources
    assert not result.shadow_sources


def test_table_structure_survives_visual_page_ocr_shadow(monkeypatch):
    _enable_field_admission(monkeypatch)
    chunk = _chunk(
        "table",
        "| 年份 | 数量 |\n|---|---|\n| 2024 | 100 |",
        metadata={
            METADATA_KEY_IMAGE_OCR_MODE: OCR_IMAGE_MODE_SHADOW_AUXILIARY,
            "ocr_text": "錯亂表格OCR",
        },
    )

    result = build_content_admission(chunk, _document())

    assert "2024" in result.content
    assert "錯亂表格OCR" not in result.content
    assert "table_text" in result.index_sources
    assert result.shadow_sources == ("visual_ocr",)


def test_legacy_visual_quarantine_is_not_a_trusted_whole_chunk_skip():
    flags = {
        "skip_embedding_recommended": True,
        "skip_reason": SKIP_REASON_VISUAL_OCR_QUARANTINE,
    }
    metadata = dict(flags)
    text = "这是一段可靠且完整的原生文本，不能被历史视觉 OCR 标记继续隔离。" * 3

    assert not should_skip_text_chunk(
        text,
        chunk_type="text",
        flags=flags,
        metadata=metadata,
    )
    clean_flags, clean_metadata, recovered = clear_legacy_visual_ocr_skip_flags(
        flags=flags,
        metadata=metadata,
    )
    assert recovered is True
    assert "skip_reason" not in clean_flags
    assert "skip_reason" not in clean_metadata


def test_apply_content_admission_clears_stale_skip_and_persists_diagnostics(
    monkeypatch,
):
    _enable_field_admission(monkeypatch)
    stale = {
        "skip_embedding_recommended": True,
        "skip_reason": SKIP_REASON_VISUAL_OCR_QUARANTINE,
    }
    chunk = _chunk(
        "image",
        "图片",
        metadata={
            **stale,
            METADATA_KEY_IMAGE_OCR_MODE: OCR_IMAGE_MODE_SHADOW_AUXILIARY,
            "vlm_description": "一幅楷书作品，文字排列规整",
            "ocr_text": "低可信OCR",
        },
        flags=stale,
    )

    result = _apply_content_admission(chunk, _document())

    assert result.content
    assert "skip_reason" not in chunk.metadata_json
    assert "skip_reason" not in chunk.flags
    assert chunk.metadata_json["legacy_visual_ocr_skip_recovered"] is True
    diagnostic = chunk.metadata_json["content_admission"]
    assert diagnostic["version"] == CONTENT_ADMISSION_VERSION
    assert diagnostic["decision"] == "index"
    assert diagnostic["index_sources"] == ["vlm_description"]
    assert diagnostic["shadow_sources"] == ["visual_ocr"]


def test_revoke_chunk_embedding_disables_local_row_and_deletes_external(
    monkeypatch,
):
    chunk = _chunk("image_ocr_text", "仅 OCR")
    update = MagicMock(return_value=1)
    query = MagicMock()
    query.filter.return_value.update = update
    db = MagicMock()
    db.query.return_value = query
    external_delete = MagicMock()
    monkeypatch.setattr(
        "app.services.embedding_service.delete_external_vector",
        external_delete,
    )

    revoked = _revoke_chunk_embedding(db, chunk)

    assert revoked == 1
    update.assert_called_once_with(
        {"is_enabled": False, "content": ""},
        synchronize_session=False,
    )
    external_delete.assert_called_once_with(db, chunk.id)


def test_embedding_and_bm25_use_the_same_field_level_content(monkeypatch):
    _enable_field_admission(monkeypatch)
    chunk = _chunk(
        "image",
        "图片",
        metadata={
            METADATA_KEY_IMAGE_OCR_MODE: OCR_IMAGE_MODE_SHADOW_AUXILIARY,
            "caption": "兰亭序局部",
            "vlm_description": "行书作品局部，带有红色印章",
            "ocr_text": "錯亂OCR不应索引",
        },
    )
    document = _document()

    embedding_content = build_embedding_content(chunk, document)
    es_doc = chunk_to_es_doc(chunk, document)

    assert es_doc["content"] == embedding_content
    assert es_doc["ocr_text"] == ""
    assert "红色印章" in es_doc["vlm_description"]
    assert _is_indexable_chunk(chunk, document) is True


def test_explicit_shadow_ocr_is_consistently_non_indexable(monkeypatch):
    _enable_field_admission(monkeypatch)
    chunk = _chunk(
        "image_ocr_text",
        "仅视觉 OCR 内容",
        metadata={METADATA_KEY_IMAGE_OCR_MODE: OCR_IMAGE_MODE_SHADOW_AUXILIARY},
    )

    assert build_embedding_content(chunk, _document()) == ""
    assert _is_indexable_chunk(chunk, _document()) is False


@pytest.mark.asyncio
async def test_embed_chunks_recovers_legacy_skip_and_embeds_vlm(monkeypatch):
    _enable_field_admission(monkeypatch)
    stale = {
        "skip_embedding_recommended": True,
        "skip_reason": SKIP_REASON_VISUAL_OCR_QUARANTINE,
    }
    chunk = _chunk(
        "image",
        "图片",
        metadata={
            **stale,
            METADATA_KEY_IMAGE_OCR_MODE: OCR_IMAGE_MODE_SHADOW_AUXILIARY,
            "vlm_description": "一幅纵向草书作品",
            "ocr_text": "錯亂OCR",
        },
        flags=stale,
    )
    document = _document()
    chunk_query = MagicMock()
    chunk_query.filter.return_value.order_by.return_value.all.return_value = [chunk]
    document_query = MagicMock()
    document_query.filter.return_value.all.return_value = [document]
    db = MagicMock()
    db.query.side_effect = lambda model: (
        chunk_query if model is Chunk else document_query
    )
    runtime = EmbeddingRuntime(
        provider="test",
        model_name="test-embedding",
        base_url="",
        dimension=3,
    )
    monkeypatch.setattr(
        "app.services.embedding_service.ensure_embedding_ready",
        AsyncMock(return_value=runtime),
    )
    embed_text = AsyncMock(return_value=[0.1, 0.2, 0.3])
    monkeypatch.setattr("app.services.embedding_service.embed_text", embed_text)
    upsert = MagicMock()
    monkeypatch.setattr("app.services.embedding_service._upsert_embedding", upsert)
    monkeypatch.setattr(
        "app.services.embedding_service._count_missing_embeddings",
        lambda _db, _chunks: 0,
    )
    keyword_sync = MagicMock(return_value=1)
    monkeypatch.setattr(
        "app.services.embedding_service._sync_keyword_documents_after_commit",
        keyword_sync,
    )

    response = await embed_chunks(db, None, [chunk.id])

    assert response["success"] is True
    embedded_text = embed_text.await_args.args[0]
    assert "纵向草书作品" in embedded_text
    assert "錯亂OCR" not in embedded_text
    upsert.assert_called_once()
    keyword_sync.assert_called_once()
    assert chunk.metadata_json["legacy_visual_ocr_skip_recovered"] is True
    db.commit.assert_called_once()


@pytest.mark.asyncio
async def test_embed_chunks_revokes_existing_vector_for_shadow_only_ocr(
    monkeypatch,
):
    _enable_field_admission(monkeypatch)
    chunk = _chunk(
        "image_ocr_text",
        "仅保留为辅助证据的草书 OCR",
        metadata={METADATA_KEY_IMAGE_OCR_MODE: OCR_IMAGE_MODE_SHADOW_AUXILIARY},
    )
    document = _document()
    chunk_query = MagicMock()
    chunk_query.filter.return_value.order_by.return_value.all.return_value = [chunk]
    document_query = MagicMock()
    document_query.filter.return_value.all.return_value = [document]
    db = MagicMock()
    db.query.side_effect = lambda model: (
        chunk_query if model is Chunk else document_query
    )
    runtime = EmbeddingRuntime(
        provider="test",
        model_name="test-embedding",
        base_url="",
        dimension=3,
    )
    monkeypatch.setattr(
        "app.services.embedding_service.ensure_embedding_ready",
        AsyncMock(return_value=runtime),
    )
    embed_text = AsyncMock()
    monkeypatch.setattr("app.services.embedding_service.embed_text", embed_text)
    revoke = MagicMock(return_value=1)
    monkeypatch.setattr(
        "app.services.embedding_service._revoke_chunk_embedding",
        revoke,
    )
    monkeypatch.setattr(
        "app.services.embedding_service._count_missing_embeddings",
        lambda _db, _chunks: 1,
    )
    monkeypatch.setattr(
        "app.services.embedding_service._sync_keyword_documents_after_commit",
        MagicMock(return_value=0),
    )

    response = await embed_chunks(db, None, [chunk.id])

    assert response["success"] is True
    assert response["data"]["skipped_chunks"] == 1
    revoke.assert_called_once_with(db, chunk)
    embed_text.assert_not_awaited()
