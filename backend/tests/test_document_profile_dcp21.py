import json
from types import SimpleNamespace
from unittest.mock import MagicMock

from app.db.models import Document, DocumentCanonicalArtifact
from app.services.document_parser.canonical_document import (
    CanonicalAsset,
    CanonicalDocument,
    CanonicalSourceNode,
)
from app.services.document_parser.document_profiler import (
    profile_canonical_document,
)
from app.services.document_parser.document_profiler_v2 import (
    profile_canonical_document_v2,
)
from app.services.document_service import get_document_processing_profile
from scripts.document_profile_dataset.generator import generate_dataset
from scripts.document_profile_dataset.replay import replay_manifest
from scripts.document_profile_dataset.report import build_coverage_report


def _canonical_page(
    text: str,
    *,
    native_text_chars: int,
    dominant_image_ratio: float,
    asset: CanonicalAsset | None = None,
) -> CanonicalDocument:
    return CanonicalDocument(
        markdown_content=text,
        provenance_nodes=[
            CanonicalSourceNode(
                node_id="text-1",
                node_type="text",
                source_order=0,
                canonical_text=text,
                text_hash="text-hash",
                page_no=1,
                bbox=(40, 60, 560, 720),
            )
        ],
        assets={asset.asset_id: asset} if asset else {},
        metadata={
            "page_visual_profile": {
                "decisions": [
                    {
                        "page_no": 1,
                        "page_width": 612,
                        "page_height": 792,
                        "native_text_chars": native_text_chars,
                        "dominant_image_ratio": dominant_image_ratio,
                        "decision": "preserve",
                    }
                ]
            }
        },
        source_format="pdf",
    )


def test_dcp21_native_text_on_visual_page_is_never_treated_as_visual_ocr():
    canonical = _canonical_page(
        "艺术家介绍：这是一幅山水作品，正文来自 PDF 原生文本层。",
        native_text_chars=28,
        dominant_image_ratio=0.82,
        asset=CanonicalAsset(
            asset_id="image-1",
            page_no=1,
            caption="山水作品",
            vlm_description="一幅山水画",
        ),
    )

    profile = profile_canonical_document_v2(
        canonical,
        profile_canonical_document(canonical),
    )
    page = profile.pages[0]

    assert page.layout_kind == "mixed_layout"
    assert page.ocr_trust_band == "not_applicable"
    assert "native_text" in page.text_origins
    assert "visual_ocr" not in page.text_origins
    assert page.origin_admission["native_text"] == "normal"
    assert page.origin_admission["caption"] == "normal"
    assert page.origin_admission["vlm_description"] == "normal"


def test_dcp21_native_text_guard_wins_over_ocr_semantic_role():
    canonical = _canonical_page(
        "可靠的 PDF 原生正文",
        native_text_chars=12,
        dominant_image_ratio=0.8,
    )
    canonical.provenance_nodes[0].semantic_role = "visual_ocr_candidate"

    page = profile_canonical_document_v2(
        canonical,
        profile_canonical_document(canonical),
    ).pages[0]

    assert page.ocr_trust_band == "not_applicable"
    assert page.text_origins == ("native_text", "parser_text")
    assert page.origin_admission == {
        "native_text": "normal",
        "parser_text": "normal",
    }


def test_dcp21_scanned_prose_and_short_visual_ocr_use_continuous_scores():
    scanned_text = (
        "本页为扫描合同正文。供应方应在三十日内交付设备，验收完成后支付款项。"
        "双方应妥善保管全部技术资料，并按照约定履行保密义务。"
    ) * 4
    scanned = _canonical_page(
        scanned_text,
        native_text_chars=0,
        dominant_image_ratio=0.91,
    )
    visual = _canonical_page(
        "遠山古木",
        native_text_chars=0,
        dominant_image_ratio=0.91,
    )

    scanned_page = profile_canonical_document_v2(
        scanned,
        profile_canonical_document(scanned),
    ).pages[0]
    visual_page = profile_canonical_document_v2(
        visual,
        profile_canonical_document(visual),
    ).pages[0]

    assert scanned_page.ocr_trust_score is not None
    assert scanned_page.ocr_trust_band in {"trusted", "auxiliary"}
    assert scanned_page.origin_admission["visual_ocr"] in {"normal", "observe"}
    assert visual_page.ocr_trust_score is not None
    assert visual_page.ocr_trust_band in {"uncertain", "shadow"}
    assert visual_page.origin_admission["visual_ocr"] == "shadow_auxiliary"
    assert all(item.get("signal") for item in visual_page.evidence)


def test_dcp21_unpunctuated_cjk_gibberish_is_shadow_auxiliary():
    gibberish = (
        "皇清洞真道经紫素骑修空本华京板五吉横反胎七元庭玄解重结景祖绝锦青能四秀"
        "大虚空元景超室九素合重虚帝君静幽谷八真合景君玄太雄雌运玉房九变逍得小形散"
        "泥丸易分别碑神善光府神梁南事五符引刻金生华日发夜月沈板尸太极元景王三天称神"
    )
    canonical = _canonical_page(
        gibberish,
        native_text_chars=0,
        dominant_image_ratio=0.91,
    )

    page = profile_canonical_document_v2(
        canonical,
        profile_canonical_document(canonical),
    ).pages[0]

    fragmentation = next(
        item
        for item in page.evidence
        if item.get("signal") == "unpunctuated_cjk_fragmentation_penalty"
    )
    assert fragmentation["value"] is True
    assert fragmentation["contribution"] < 0
    assert page.ocr_trust_band in {"uncertain", "shadow"}
    assert page.origin_admission["visual_ocr"] == "shadow_auxiliary"


def test_dcp21_coherent_unpunctuated_cjk_is_not_fragmentation():
    coherent = (
        "天地之大德曰生圣人之大宝曰位何以守位曰仁何以聚人曰财"
        "理财正辞禁民为非曰义天地之大德曰生圣人之大宝曰位何以守位曰仁"
        "何以聚人曰财理财正辞禁民为非曰义"
    )
    canonical = _canonical_page(
        coherent,
        native_text_chars=0,
        dominant_image_ratio=0.91,
    )

    page = profile_canonical_document_v2(
        canonical,
        profile_canonical_document(canonical),
    ).pages[0]

    fragmentation = next(
        item
        for item in page.evidence
        if item.get("signal") == "unpunctuated_cjk_fragmentation_penalty"
    )
    assert fragmentation["value"] is False
    assert fragmentation["contribution"] == 0


def test_dcp21_page_less_text_is_used_for_quality_scoring():
    text = "This scanned paragraph has enough words and punctuation to be continuous prose. " * 4
    canonical = CanonicalDocument(
        markdown_content=text,
        provenance_nodes=[
            CanonicalSourceNode(
                node_id="logical-text",
                node_type="text",
                source_order=0,
                canonical_text=text,
                text_hash="logical",
            )
        ],
        source_format="markdown",
    )

    profile = profile_canonical_document_v2(
        canonical,
        profile_canonical_document(canonical),
    )

    quality = next(
        item
        for item in profile.pages[0].evidence
        if item.get("signal") == "valid_character_ratio"
    )
    assert quality["value"] > 0.9


def test_dcp21_visual_items_make_parser_text_an_ocr_candidate():
    canonical = _canonical_page(
        "春江花月夜",
        native_text_chars=0,
        dominant_image_ratio=0.34,
    )
    visual_layout = {
        "pages": [
            {
                "page_no": 1,
                "visual_items": [
                    {"visual_item_id": "item-1", "status": "resolved"},
                    {"visual_item_id": "item-2", "status": "resolved"},
                ],
                "text_relations": [],
            }
        ]
    }

    page = profile_canonical_document_v2(
        canonical,
        profile_canonical_document(canonical),
        visual_layout=visual_layout,
    ).pages[0]

    assert "visual_ocr" in page.text_origins
    assert page.ocr_trust_band != "not_applicable"
    assert page.origin_admission["visual_ocr"] == "shadow_auxiliary"


def test_dcp21_unsegmented_visual_is_counted_as_uncertain():
    canonical = _canonical_page(
        "山水",
        native_text_chars=0,
        dominant_image_ratio=0.9,
    )
    visual_layout = {
        "pages": [
            {
                "page_no": 1,
                "visual_items": [
                    {
                        "visual_item_id": "item-1",
                        "status": "unsegmented_page_visual",
                    }
                ],
                "text_relations": [],
            }
        ]
    }

    profile = profile_canonical_document_v2(
        canonical,
        profile_canonical_document(canonical),
        visual_layout=visual_layout,
    )

    assert profile.pages[0].uncertain is True
    assert "unsegmented_page_visual" in profile.pages[0].uncertainty_reasons
    assert profile.uncertain_page_count == 1


def test_dcp21_processing_profile_api_supports_page_filter():
    document = SimpleNamespace(id="doc-1")
    artifact = SimpleNamespace(
        parse_revision="revision-1",
        stats_json={
            "document_profile_v2": {
                "profile_version": "document-profile-v2",
                "shadow_only": True,
                "page_count": 2,
                "layout_distribution": {"mixed_layout": 1.0},
                "pages": [
                    {"page_no": 1, "layout_kind": "mixed_layout"},
                    {"page_no": 2, "layout_kind": "text_dominant"},
                ],
                "evidence": [{"signal": "shadow_only", "value": True}],
            },
            "document_profile": {
                "pages": [
                    {"page_no": 1, "page_kind": "visual_dominant"},
                    {"page_no": 2, "page_kind": "native_text"},
                ]
            },
        },
    )
    db = MagicMock()

    def get(model, _key):
        if model is Document:
            return document
        if model is DocumentCanonicalArtifact:
            return artifact
        raise AssertionError(f"unexpected model: {model}")

    db.get.side_effect = get
    index_response = get_document_processing_profile(db, "doc-1")
    response = get_document_processing_profile(db, "doc-1", page_no=2)

    assert index_response["data"]["pages"] == []
    assert index_response["data"]["legacy_pages"] == []
    assert [page["page_no"] for page in index_response["data"]["available_pages"]] == [
        1,
        2,
    ]
    assert response["success"] is True
    assert response["data"]["shadow_only"] is True
    assert [page["page_no"] for page in response["data"]["pages"]] == [2]
    assert [page["page_no"] for page in response["data"]["legacy_pages"]] == [2]
    assert "pages" not in response["data"]["summary"]


def test_dcp21_dataset_gate_and_shadow_metadata_are_complete(tmp_path):
    manifest = generate_dataset(tmp_path, seed=211)
    snapshots = replay_manifest(tmp_path, manifest)
    report = build_coverage_report(tmp_path, manifest)

    dcp21 = report["document_profile_dcp21"]
    assert dcp21["gate_passed"] is True, json.dumps(
        {
            "gate": dcp21,
            "poster_cases": [
                case
                for case in report["adversarial_dataset"]["cases"]
                if case["confusion_group"] == "poster-char-count"
            ],
        },
        ensure_ascii=False,
        indent=2,
    )
    assert report["document_profile_dcp21"]["profiled_adversarial_pages"] == 31
    assert report["document_profile_dcp21"][
        "visual_ocr_candidate_gate_passed"
    ] is True
    assert report["document_profile_dcp21"][
        "cjk_gibberish_gate_passed"
    ] is True
    assert all(
        check["passed"]
        for check in report["document_profile_dcp21"]["perturbation_checks"]
    )
    assert report["engineering_baseline_passed"] is True
    for snapshot in snapshots:
        stats = snapshot["layers"]["canonical_artifact"]["stats_json"]
        assert stats["document_profile_v2"]["shadow_only"] is True
        for chunk in snapshot["layers"]["chunk_metadata"]:
            if chunk.get("chunk_type") == "parent_text":
                continue
            metadata = chunk.get("metadata_json") or {}
            if not (
                metadata.get("page_no")
                or metadata.get("chunk_policy_page_no")
            ):
                continue
            assert metadata.get("page_profile_v2")
            assert metadata.get("content_admission_shadow_v2", {}).get(
                "shadow_only"
            ) is True
