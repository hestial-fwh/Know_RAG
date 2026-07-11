from app.services.chunker.canonical_parent_child import (
    CanonicalChildDraft,
    deduplicate_overlap_shadow_children,
)


def _text_child(
    content: str,
    *,
    seq: int,
    start: int,
    end: int,
    header: str,
    parent_index: int | None = 0,
) -> CanonicalChildDraft:
    return CanonicalChildDraft(
        content=content,
        chunk_type="text",
        parent_index=parent_index,
        context_header=header,
        start=start,
        end=end,
        source_child_seq=seq,
        metadata={"content_kind": "text"},
        flags={"content_kind": "text"},
    )


def test_previous_short_chunk_fully_repeated_as_next_prefix_is_removed_even_when_headers_differ():
    shadow = _text_child(
        "---\n\n## 6. 数据库设计\n\n**文件**: `app/db/models.py`\n\n### ER 图（简化）",
        seq=30,
        start=10103,
        end=10691,
        header="# maikb_rag 后端架构与开发指南\n## 5. 配置系统",
    )
    carrier = _text_child(
        "---\n\n## 6. 数据库设计\n\n**文件**: `app/db/models.py`\n\n### ER 图（简化）\n\n```text\nUser (1) ─────\n│\n▼\nAuditLog\n```",
        seq=31,
        start=10103,
        end=11080,
        header="# maikb_rag 后端架构与开发指南\n## 6. 数据库设计",
    )

    result = deduplicate_overlap_shadow_children([shadow, carrier])

    assert len(result) == 1
    assert result[0].source_child_seq == 31
    assert result[0].metadata["overlap_shadow_dedup_applied"] is True
    assert result[0].metadata["deduplicated_overlap_shadow_source_child_seqs"] == [30]
    assert result[0].metadata["overlap_shadow_last_direction"] == "left_shadowed_by_right"


def test_repeated_relationship_prelude_shadow_is_removed():
    shadow = _text_child(
        "> **注意**: 向量唯一存储在 Milvus 中。`chunk_embeddings` 仅作为索引追踪表。\n\n### 关键关系",
        seq=47,
        start=15126,
        end=15720,
        header="# 指南\n## 6. 数据库设计\n### 12 张数据表\n### 11. chunk_embeddings",
    )
    carrier = _text_child(
        "> **注意**: 向量唯一存储在 Milvus 中。`chunk_embeddings` 仅作为索引追踪表。\n\n### 关键关系\n\n```text\nKnowledgeBase ← Document ← Chunk ← ChunkEmbedding\n```",
        seq=48,
        start=15126,
        end=16050,
        header="# 指南\n## 6. 数据库设计\n### 关键关系",
    )

    result = deduplicate_overlap_shadow_children([shadow, carrier])

    assert len(result) == 1
    assert "KnowledgeBase" in result[0].content
    assert result[0].metadata["overlap_shadow_dedup_count"] == 1


def test_same_text_without_source_span_overlap_is_not_deleted():
    first = _text_child(
        "## 常见说明\n\n这是一段可能在文档中重复出现的模板文字。",
        seq=1,
        start=0,
        end=80,
        header="## 第一章",
    )
    second = _text_child(
        "## 常见说明\n\n这是一段可能在文档中重复出现的模板文字。\n\n第二处独立正文。",
        seq=2,
        start=500,
        end=650,
        header="## 第二章",
    )

    result = deduplicate_overlap_shadow_children([first, second])

    assert len(result) == 2


def test_partial_overlap_with_independent_semantics_is_not_deleted():
    first = _text_child(
        "上一节仍有独立结论，不能删除。\n\n## 下一节\n\n短引言。",
        seq=3,
        start=100,
        end=220,
        header="## 上一节",
    )
    second = _text_child(
        "## 下一节\n\n短引言。\n\n后续主体内容。",
        seq=4,
        start=170,
        end=310,
        header="## 下一节",
    )

    result = deduplicate_overlap_shadow_children([first, second])

    assert len(result) == 2
    assert "上一节仍有独立结论" in result[0].content


def test_same_source_child_materialization_fragments_are_not_deduplicated():
    first = _text_child(
        "短说明。",
        seq=8,
        start=1000,
        end=1200,
        header="## 同一章节",
    )
    second = _text_child(
        "短说明。\n\n后续正文。",
        seq=8,
        start=1000,
        end=1200,
        header="## 同一章节",
    )

    result = deduplicate_overlap_shadow_children([first, second])

    assert len(result) == 2


def test_reverse_shadow_can_be_removed_defensively():
    carrier = _text_child(
        "主体正文。\n\n## 下一节\n\n短引言。",
        seq=10,
        start=100,
        end=300,
        header="## 当前节",
    )
    shadow = _text_child(
        "## 下一节\n\n短引言。",
        seq=11,
        start=240,
        end=300,
        header="## 下一节",
    )

    result = deduplicate_overlap_shadow_children([carrier, shadow])

    assert len(result) == 1
    assert result[0].source_child_seq == 10
    assert result[0].metadata["overlap_shadow_last_direction"] == "right_shadowed_by_left"


def test_plan_builder_runs_overlap_shadow_dedup_before_parent_validation(monkeypatch):
    from types import SimpleNamespace
    import app.services.chunker.canonical_parent_child as module
    from app.services.chunker.models import ChunkerConfig
    from app.services.document_parser.canonical_document import CanonicalDocument

    shadow_text = "## 6. 数据库设计\n\n**文件**: `app/db/models.py`\n\n### ER 图（简化）"
    carrier_text = shadow_text + "\n\n后续真正的 ER 图正文。"
    fake_result = SimpleNamespace(
        parents=[
            SimpleNamespace(
                content=carrier_text,
                context_header="## 6. 数据库设计",
                start=100,
                end=500,
                metadata={},
            )
        ],
        children=[
            SimpleNamespace(
                content=shadow_text,
                context_header="## 5. 配置系统",
                start=100,
                end=300,
                seq=30,
                parent_index=0,
                metadata={},
            ),
            SimpleNamespace(
                content=carrier_text,
                context_header="## 6. 数据库设计",
                start=100,
                end=500,
                seq=31,
                parent_index=0,
                metadata={},
            ),
        ],
    )
    monkeypatch.setattr(module, "split_parent_child_result", lambda markdown, cfg: fake_result)

    plan = module.build_canonical_parent_child_plan(
        CanonicalDocument(markdown_content=carrier_text, metadata={"canonical_source": "native_markdown"}, source_format="md"),
        ChunkerConfig(enable_parent_child=True, child_chunk_size=384, parent_chunk_size=4096),
    )

    assert len(plan.children) == 1
    assert len(plan.parents) == 0  # parent collapses after the shadow child is removed
    assert "后续真正的 ER 图正文" in plan.children[0].content
    assert plan.children[0].metadata["overlap_shadow_dedup_applied"] is True
