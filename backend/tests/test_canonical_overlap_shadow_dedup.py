from app.services.chunker.canonical_parent_child import (
    CanonicalChildDraft,
    deduplicate_repeated_asset_children,
    deduplicate_overlap_shadow_children,
    merge_overlap_tail_text_children,
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


def test_same_asset_copied_by_overlap_is_materialized_only_once():
    first = CanonicalChildDraft(
        content="图片标题：UV-vis 光谱图",
        chunk_type="image",
        parent_index=0,
        context_header="## 结构表征分析",
        start=100,
        end=300,
        source_child_seq=21,
        metadata={"asset_id": "image-0001-fixed", "content_kind": "image"},
        image_info={"asset_id": "image-0001-fixed"},
        flags={"content_kind": "image"},
    )
    repeated = CanonicalChildDraft(
        content=first.content,
        chunk_type="image",
        parent_index=0,
        context_header=first.context_header,
        start=220,
        end=420,
        source_child_seq=22,
        metadata={"asset_id": "image-0001-fixed", "content_kind": "image"},
        image_info={"asset_id": "image-0001-fixed"},
        flags={"content_kind": "image"},
    )

    result = deduplicate_repeated_asset_children([first, repeated])

    assert result == [first]
    assert first.metadata["asset_overlap_dedup_applied"] is True
    assert first.metadata["deduplicated_asset_source_child_seqs"] == [22]


def test_same_asset_reference_at_independent_source_position_is_preserved():
    first = CanonicalChildDraft(
        content="图片说明",
        chunk_type="image",
        parent_index=None,
        context_header="## 第一处",
        start=0,
        end=100,
        source_child_seq=1,
        metadata={"asset_id": "shared-asset", "content_kind": "image"},
        flags={"content_kind": "image"},
    )
    second = CanonicalChildDraft(
        content="图片说明",
        chunk_type="image",
        parent_index=None,
        context_header="## 第二处",
        start=500,
        end=600,
        source_child_seq=8,
        metadata={"asset_id": "shared-asset", "content_kind": "image"},
        flags={"content_kind": "image"},
    )

    assert deduplicate_repeated_asset_children([first, second]) == [first, second]


def test_short_overlap_tail_is_merged_without_repeating_the_overlap():
    repeated_prefix = "上一块末尾的完整重叠句子，用来验证精确边界。"
    left = _text_child(
        "主体内容。" * 60 + repeated_prefix,
        seq=100,
        start=25825,
        end=26414,
        header="## Activity analysis",
    )
    right = _text_child(
        repeated_prefix + "最后一句补充结论。",
        seq=101,
        start=26314,
        end=26432,
        header="## Activity analysis",
    )

    result = merge_overlap_tail_text_children([left, right], child_size=600)

    assert len(result) == 1
    assert result[0].content.count(repeated_prefix) == 1
    assert result[0].content.endswith("最后一句补充结论。")
    assert result[0].metadata["overlap_tail_merged"] is True


def test_short_tail_without_source_overlap_is_not_merged():
    repeated_prefix = "文本相同也不能仅凭内容跨位置合并。"
    left = _text_child(
        "主体内容。" * 30 + repeated_prefix,
        seq=1,
        start=0,
        end=300,
        header="## 同一章节",
    )
    right = _text_child(
        repeated_prefix + "另一处结论。",
        seq=2,
        start=500,
        end=550,
        header="## 同一章节",
    )

    assert merge_overlap_tail_text_children([left, right], child_size=600) == [left, right]


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
