import json
from pathlib import Path

from app.db.models import Chunk
from app.services.chunk_service import _enforce_parent_child_integrity
from app.services.chunker.canonical_parent_child import (
    CanonicalChildDraft,
    CanonicalChunkPlan,
    CanonicalParentDraft,
)
from app.services.chunker.chunk_policy_resolver import (
    DCP3_ACTIVE_POLICY_IDS,
    DCP31_POLICY_VERSION,
    resolve_chunk_policies,
)
from app.services.chunker.dcp31_parent_policy import (
    apply_visual_item_parent_policy,
)
from app.services.chunker.models import ChunkerConfig
from app.services.document_parser.canonical_document import CanonicalDocument
from app.services.document_parser.document_profiler import (
    profile_canonical_document,
)
from app.services.document_parser.document_profiler_v2 import (
    profile_canonical_document_v2,
)
from app.services.document_parser.mineru_ocr_admission import (
    ALGORITHM_VERSION,
    annotate_mineru_block_origins,
    apply_mineru_ocr_admission,
)
from app.services.document_parser.types import ParsedBlock, ParsedDocument


FIXTURE = (
    Path(__file__).parent
    / "fixtures"
    / "dcp31_calligraphy_replay_page7.json"
)


def _mineru_document(blocks: list[ParsedBlock]) -> ParsedDocument:
    return ParsedDocument(
        content="\n\n".join(block.text for block in blocks),
        blocks=blocks,
        metadata={
            "parser": "mineru",
            "page_visual_profile": {
                "decisions": [
                    {
                        "page_no": 7,
                        "native_text_chars": 157,
                        "dominant_image_ratio": 1.0,
                        "decision": "preserve",
                    }
                ]
            },
        },
    )


def _text_block(
    text: str,
    bbox: tuple[float, float, float, float],
    *,
    order: int,
) -> ParsedBlock:
    return ParsedBlock(
        block_type="text",
        text=text,
        page_no=7,
        bbox=bbox,
        order_index=order,
        metadata={
            "parser": "mineru",
            "source": "mineru_content_list",
            "raw_block_type": "text",
        },
    )


def test_dcp31_real_calligraphy_replay_keeps_printed_text_and_shadows_artwork_ocr(
    monkeypatch,
):
    fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
    blocks = [
        _text_block(
            item["text"],
            tuple(item["bbox"]),
            order=index,
        )
        for index, item in enumerate(fixture["blocks"])
    ]
    blocks.append(
        ParsedBlock(
            block_type="image",
            text="PDF 第 7 页主视觉",
            page_no=7,
            bbox=(0, 0, 1000, 800),
            order_index=len(blocks),
            image_path="page-7.jpg",
            metadata={
                "source": "pdf_page_visual_guard",
                "semantic_role": "page_primary_visual",
                "vlm_description": fixture["vlm_description"],
            },
        )
    )
    document = _mineru_document(blocks)
    monkeypatch.setattr(
        "app.services.document_parser.mineru_ocr_admission._native_text_by_page",
        lambda _path: {7: fixture["native_text"]},
    )

    annotated = annotate_mineru_block_origins(Path("calligraphy.pdf"), document)
    assert annotated.blocks[0].metadata["text_origin"] == "native_text"
    assert all(
        block.metadata["text_origin"] == "mineru_ocr"
        for block in annotated.blocks[1:4]
    )

    projected = apply_mineru_ocr_admission(annotated, enabled=True)
    remaining_text = [
        block for block in projected.blocks if block.block_type == "text"
    ]
    summary = projected.metadata["mineru_ocr_admission"]

    assert len(remaining_text) == 1
    assert "此作书于元大德九年" in remaining_text[0].text
    assert summary["algorithm_version"] == ALGORITHM_VERSION
    assert summary["admission_counts"]["shadow"] == 3
    assert summary["excluded_block_count"] == 3
    assert not any(
        "太徽天帝君" in block.text for block in remaining_text
    )
    assert any(block.block_type == "image" for block in projected.blocks)


def test_dcp31_scanned_horizontal_prose_remains_in_production_projection():
    text = (
        "本页为扫描合同正文。供应方应在三十日内交付设备，验收完成后支付款项。"
        "双方应妥善保管全部技术资料，并按照约定履行保密义务。"
    ) * 3
    block = _text_block(text, (60, 80, 940, 420), order=0)
    block.page_no = 1
    block.metadata["text_origin"] = "mineru_ocr"
    document = ParsedDocument(
        content=text,
        blocks=[block],
        metadata={
            "parser": "mineru",
            "page_visual_profile": {
                "decisions": [
                    {
                        "page_no": 1,
                        "native_text_chars": 0,
                        "dominant_image_ratio": 0.92,
                        "decision": "preserve",
                    }
                ]
            },
        },
    )

    projected = apply_mineru_ocr_admission(document, enabled=True)

    assert len(projected.blocks) == 1
    assert projected.blocks[0].metadata["ocr_admission"] == "normal"
    assert projected.metadata["mineru_ocr_admission"]["excluded_block_count"] == 0


def test_dcp31_native_printed_header_is_not_discarded_as_parser_noise(monkeypatch):
    block = _text_block(
        "姚绶 书送张元诗并序卷 明纸本行书纵28、横350厘米",
        (83, 65, 376, 123),
        order=0,
    )
    block.metadata["raw_block_type"] = "header"
    document = _mineru_document([block])
    monkeypatch.setattr(
        "app.services.document_parser.mineru_ocr_admission._native_text_by_page",
        lambda _path: {7: block.text},
    )

    annotated = annotate_mineru_block_origins(Path("calligraphy.pdf"), document)
    projected = apply_mineru_ocr_admission(annotated, enabled=True)

    assert annotated.blocks[0].metadata["text_origin"] == "native_text"
    assert projected.blocks[0].text == block.text


def test_dcp31_calligraphy_hint_never_promotes_ocr_to_normal():
    text = (
        "少山村阪死明长喝修昆侖活著啦衣星元年七月東坡平生多與雲元本之者人州获典，"
        "富东梦言立布人於酒克之善太白去作气记其人以國，股大時又云西山有記。"
    ) * 2
    block = _text_block(text, (0, 244, 998, 425), order=0)
    block.page_no = 1
    block.metadata["text_origin"] = "mineru_ocr"
    image = ParsedBlock(
        block_type="image",
        text="书法作品",
        page_no=1,
        bbox=(0, 0, 1000, 900),
        order_index=1,
        metadata={"vlm_description": "中国传统草书书法手卷"},
    )
    document = ParsedDocument(
        content=text,
        blocks=[block, image],
        metadata={
            "parser": "mineru",
            "page_visual_profile": {
                "decisions": [
                    {
                        "page_no": 1,
                        "native_text_chars": 0,
                        "dominant_image_ratio": 1.0,
                        "decision": "preserve",
                    }
                ]
            },
        },
    )

    projected = apply_mineru_ocr_admission(document, enabled=True)

    assert all(item.block_type != "text" for item in projected.blocks)
    assert projected.metadata["mineru_ocr_admission"]["admission_counts"]["shadow"] == 1


def test_dcp31_disabled_projection_preserves_content_and_blocks():
    block = _text_block("低质量候选文本", (10, 10, 30, 200), order=0)
    block.metadata["text_origin"] = "mineru_ocr"
    document = _mineru_document([block])

    projected = apply_mineru_ocr_admission(document, enabled=False)

    assert projected.content == document.content
    assert projected.blocks == document.blocks
    assert projected.metadata["mineru_ocr_admission"][
        "production_projection_applied"
    ] is False


def _child(
    content: str,
    *,
    asset_id: str = "",
    node_id: str = "",
    parent_index: int = 0,
    start: int,
) -> CanonicalChildDraft:
    return CanonicalChildDraft(
        content=content,
        chunk_type="image" if asset_id else "text",
        parent_index=parent_index,
        context_header="作品页",
        start=start,
        end=start + len(content),
        source_child_seq=start,
        metadata={
            "page_no": 1,
            "asset_id": asset_id,
            "source_node_ids": [node_id] if node_id else [],
        },
    )


def test_dcp31_visual_items_never_share_one_parent():
    children = [
        _child("作品一说明", node_id="text-1", start=0),
        _child("作品一图片", asset_id="asset-1", start=20),
        _child("作品二说明", node_id="text-2", start=40),
        _child("作品二图片", asset_id="asset-2", start=60),
    ]
    plan = CanonicalChunkPlan(
        markdown_content="\n\n".join(child.content for child in children),
        parents=[
            CanonicalParentDraft(
                parent_index=0,
                content="错误地混合两个作品",
                context_header="作品页",
                start=0,
                end=80,
            )
        ],
        children=children,
    )
    layout = {
        "pages": [
            {
                "page_no": 1,
                "visual_items": [
                    {
                        "visual_item_id": "item-1",
                        "asset_ids": ["asset-1"],
                    },
                    {
                        "visual_item_id": "item-2",
                        "asset_ids": ["asset-2"],
                    },
                ],
                "text_relations": [
                    {
                        "source_node_id": "text-1",
                        "visual_item_ids": ["item-1"],
                        "status": "bound",
                    },
                    {
                        "source_node_id": "text-2",
                        "visual_item_ids": ["item-2"],
                        "status": "bound",
                    },
                ],
            }
        ]
    }

    summary = apply_visual_item_parent_policy(plan, layout, enabled=True)

    assert summary["applied"] is True
    assert summary["visual_item_parent_count"] == 2
    assert len(plan.parents) == 2
    assert {child.parent_index for child in plan.children[:2]} == {0}
    assert {child.parent_index for child in plan.children[2:]} == {1}
    assert all(
        parent.metadata["visual_item_scoped_parent"] is True
        for parent in plan.parents
    )


def test_dcp31_ambiguous_fallback_uses_page_overview_parent():
    children = [
        _child("作品一图片", asset_id="asset-1", start=0),
        _child("作品二图片", asset_id="asset-2", start=20),
        _child("整页 fallback", asset_id="page-fallback", start=40),
    ]
    plan = CanonicalChunkPlan(
        markdown_content="\n\n".join(child.content for child in children),
        parents=[
            CanonicalParentDraft(
                parent_index=0,
                content="旧页面父块",
                context_header="作品页",
                start=0,
                end=60,
            )
        ],
        children=children,
    )
    layout = {
        "pages": [
            {
                "page_no": 1,
                "visual_items": [
                    {
                        "visual_item_id": "item-1",
                        "asset_ids": ["asset-1"],
                    },
                    {
                        "visual_item_id": "item-2",
                        "asset_ids": ["asset-2"],
                    },
                ],
                "text_relations": [],
            }
        ]
    }

    summary = apply_visual_item_parent_policy(plan, layout, enabled=True)

    fallback = next(child for child in plan.children if child.content == "整页 fallback")
    parent = plan.parents[fallback.parent_index]
    assert fallback.parent_index is not None
    assert fallback.flags.get("standalone_root_child") is not True
    assert parent.metadata["dcp31_page_overview_parent"] is True
    assert parent.metadata["visual_page_scoped_parent"] is True
    assert summary["page_overview_parent_count"] == 1
    assert summary["ambiguous_standalone_count"] == 0


def _integrity_chunk(
    chunk_id: str,
    *,
    chunk_type: str,
    parent_id: str | None = None,
    metadata: dict | None = None,
) -> Chunk:
    return Chunk(
        id=chunk_id,
        document_id="doc-1",
        knowledge_base_id="kb-1",
        content=chunk_id,
        chunk_index=0,
        chunk_type=chunk_type,
        parent_chunk_id=parent_id,
        metadata_json=metadata or {},
        flags={},
    )


def test_dcp31_integrity_keeps_single_visual_item_parent():
    parent = _integrity_chunk(
        "parent-1",
        chunk_type="parent_text",
        metadata={
            "visual_item_scoped_parent": True,
            "parent_policy": {
                "strategy": "visual_item",
                "materialize": True,
            },
        },
    )
    child = _integrity_chunk(
        "child-1",
        chunk_type="image",
        parent_id=parent.id,
        metadata={"visual_item_scoped_parent": True},
    )

    repaired = _enforce_parent_child_integrity([parent, child])

    assert [chunk.id for chunk in repaired] == ["parent-1", "child-1"]
    assert child.parent_chunk_id == parent.id


def test_dcp31_resolved_policy_exposes_three_composable_decisions():
    canonical = CanonicalDocument(
        markdown_content="作品页",
        metadata={
            "page_visual_profile": {
                "decisions": [
                    {
                        "page_no": 1,
                        "native_text_chars": 0,
                        "dominant_image_ratio": 0.9,
                        "decision": "preserve",
                    }
                ]
            }
        },
        source_format="pdf",
    )
    profile_v1 = profile_canonical_document(canonical)
    profile_v2 = profile_canonical_document_v2(canonical, profile_v1)
    policies = resolve_chunk_policies(
        profile_v1,
        ChunkerConfig(enable_parent_child=True),
        active_policy_ids=DCP3_ACTIVE_POLICY_IDS,
        profile_v2=profile_v2,
        visual_layout={
            "pages": [
                {
                    "page_no": 1,
                    "visual_items": [{"visual_item_id": "item-1"}],
                }
            ]
        },
        policy_v2_enabled=True,
    )

    policy = policies[0]
    assert policy.policy_version == DCP31_POLICY_VERSION
    assert policy.boundary_policy.strategy
    assert policy.parent_policy.strategy == "visual_item"
    assert policy.content_admission_policy.stage == "pre_reflow_and_index"
