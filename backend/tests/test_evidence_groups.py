"""Unit tests for the evidence-groups payload builder.

Covers the 12 acceptance cases from the Evidence Group design:
grouping by parent (not whole-parent return), neighbor windows, far-apart
splitting, standalone roots, image priority (direct > figure > same_parent),
parent hit-ratio tie-breaking, global media dedup with evidence merge,
multi-group media ownership, and table/equation safety.
"""

import pytest

from app.schemas.evidence_groups import EvidenceGroupsResponse
from app.services.llm_context_builder import (
    _cloud_asset_id,
    _cloud_clean_content,
    _cloud_is_low_information_chunk,
    _evidence_assign_media,
    _new_media_candidate,
    build_evidence_groups_payload,
)


# ──────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────

def _text(label: str) -> str:
    return (
        f"{label}：本研究围绕{label}建立了完整的实验方法与分析流程，"
        f"通过多组对照实验验证了关键结论，并对数据进行了系统的统计分析与讨论。"
    )


def _hit(chunk_id, parent_id, *, chunk_index=None, score=0.9, chunk_type="text", metadata=None):
    return {
        "chunk_id": chunk_id,
        "parent_chunk_id": parent_id,
        "chunk_index": chunk_index,
        "chunk_type": chunk_type,
        "content": _text(chunk_id),
        "score": score,
        "metadata": metadata or {},
        "image_info": {},
    }


def _child(chunk_id, chunk_index, *, chunk_type="text", content=None, metadata=None):
    text = content if content is not None else _text(chunk_id)
    return {
        "chunk_id": chunk_id,
        "chunk_index": chunk_index,
        "chunk_type": chunk_type,
        "content_preview": text,
        "display_preview": text,
        "metadata": metadata or {},
        "image_info": {},
    }


def _img_child(chunk_id, chunk_index, asset_id, *, caption=None, metadata=None):
    meta = {"asset_id": asset_id}
    if caption:
        meta["caption"] = caption
    if metadata:
        meta.update(metadata)
    return {
        "chunk_id": chunk_id,
        "chunk_index": chunk_index,
        "chunk_type": "image",
        "content_preview": "",
        "display_preview": "",
        "metadata": meta,
        "image_info": {},
    }


def _img_hit(chunk_id, parent_id, asset_id, *, score=0.8, chunk_index=None):
    return {
        "chunk_id": chunk_id,
        "parent_chunk_id": parent_id,
        "chunk_index": chunk_index,
        "chunk_type": "image",
        "content": "",
        "score": score,
        "metadata": {
            "asset_id": asset_id,
            "caption": f"{chunk_id} 的图注说明，展示了实验流程、关键结果与结构示意图。",
        },
        "image_info": {},
    }


def _group(parent_id, children, *, matched=None, binding=None):
    return {
        "parent_chunk_id": parent_id,
        "parent_key": parent_id,
        "parent_children": children,
        "matched_children": matched or [],
        "binding_related_chunks": binding or [],
    }


_OCR_CONTEXT = (
    "失彩家月半帖星一王文協家卧事帖与此而三耳的谓心陰衣此右軍廿七帖為長洲轉宗伯收藏去年春始獲見之"
    "今又從辰玉内翰索觀尋繹舟三往得其異趣真所謂從容中道音米元章云世人以努張為筋骨不知不努張自有筋骨"
    "馬予幸得每靚神物益信此語之妙解甲辰春三月婁堅题"
)


def _img_child_with_context(chunk_id, chunk_index, asset_id):
    """Image child whose content carries OCR/context under the 上下文： field label."""
    content = f"章节：王羲之 草书寒切帖卷东晋\n图片标题：PDF 第 1 页主视觉\n上下文：{_OCR_CONTEXT}"
    return {
        "chunk_id": chunk_id,
        "chunk_index": chunk_index,
        "chunk_type": "image",
        "content_preview": content,
        "display_preview": content,
        "metadata": {"asset_id": asset_id},
        "image_info": {},
    }


def _chunk_ids(groups):
    return [g["group_id"] for g in groups]


# ──────────────────────────────────────────────────────────────────────
# _cloud_asset_id
# ──────────────────────────────────────────────────────────────────────

class TestCloudAssetId:
    def test_reads_asset_id_from_metadata(self):
        assert _cloud_asset_id({"metadata": {"asset_id": "a1"}}) == "a1"

    def test_reads_canonical_asset_id(self):
        assert _cloud_asset_id({"metadata": {"canonical_asset_id": "a2"}}) == "a2"

    def test_reads_from_image_info(self):
        assert _cloud_asset_id({"image_info": {"asset_id": "a3"}}) == "a3"

    def test_empty_when_absent(self):
        assert _cloud_asset_id({}) == ""
        assert _cloud_asset_id(None) == ""


# ──────────────────────────────────────────────────────────────────────
# Grouping / neighbor / split / standalone
# ──────────────────────────────────────────────────────────────────────

class TestEvidenceGrouping:
    def test_case1_single_hit_returns_hit_plus_neighbors_not_whole_parent(self):
        children = [_child(f"c{i}", i) for i in range(1, 8)]  # c1..c7
        data = {
            "results": [_hit("c3", "P", chunk_index=3)],
            "parent_groups": [_group("P", children)],
        }
        payload = build_evidence_groups_payload(data)
        assert len(payload["groups"]) == 1
        chunks = payload["groups"][0]["chunks"]
        ids = [c["chunk_id"] for c in chunks]
        # hit + ±1 neighbors only, NOT the whole parent (7 children)
        assert ids == ["c3", "c2", "c4"]

    def test_case2_adjacent_hits_merge_window_no_duplicate(self):
        children = [_child(f"c{i}", i) for i in range(1, 8)]
        data = {
            "results": [
                _hit("c3", "P", chunk_index=3, score=0.9),
                _hit("c5", "P", chunk_index=5, score=0.8),
            ],
            "parent_groups": [_group("P", children)],
        }
        payload = build_evidence_groups_payload(data)
        group = payload["groups"][0]
        ids = [c["chunk_id"] for c in group["chunks"]]
        # hits first, then neighbors c2/c4/c6; no duplicate c4
        assert ids == ["c3", "c5", "c2", "c4", "c6"]
        assert len(ids) == len(set(ids))

    def test_case3_far_apart_hits_split_into_two_groups(self):
        children = [_child(f"c{i}", i) for i in range(1, 22)]  # c1..c21
        data = {
            "results": [
                _hit("c2", "P", chunk_index=2, score=0.9),
                _hit("c20", "P", chunk_index=20, score=0.8),
            ],
            "parent_groups": [_group("P", children)],
        }
        payload = build_evidence_groups_payload(data)
        assert len(payload["groups"]) == 2
        assert payload["groups"][0]["chunks"][0]["chunk_id"] == "c2"
        assert payload["groups"][1]["chunks"][0]["chunk_id"] == "c20"

    def test_case4_standalone_root_forms_independent_group(self):
        data = {
            "results": [_hit("standalone1", None)],
            "parent_groups": [],
        }
        payload = build_evidence_groups_payload(data)
        assert len(payload["groups"]) == 1
        group = payload["groups"][0]
        assert group["parent_chunk_id"] is None
        assert group["chunks"][0]["chunk_id"] == "standalone1"
        assert group["chunks"][0]["direct_hit"] is True


# ──────────────────────────────────────────────────────────────────────
# Image priority (direct > figure > same_parent)
# ──────────────────────────────────────────────────────────────────────

class TestEvidenceImagePriority:
    def test_case5_image_direct_hit_is_p1(self):
        data = {
            "results": [_img_hit("img1", "P", "asset-a")],
            "parent_groups": [_group("P", [])],
        }
        payload = build_evidence_groups_payload(data)
        assert len(payload["groups"]) == 1
        media = payload["groups"][0]["media"]
        assert len(media) == 1
        assert media[0]["primary_reason"] == "direct_hit"

    def test_case6_text_hit_with_figure_link_is_p2(self):
        img = _img_child("img1", 2, "asset-a", caption="图5 实验流程示意。")
        children = [
            _child("c1", 1),
            img,
            _child("c3", 3),
        ]
        hit = _hit(
            "c1",
            "P",
            chunk_index=1,
            metadata={"figure_references": [{"image_chunk_ids": ["img1"], "figure_id": "图5", "binding_score": 0.9}]},
        )
        data = {
            "results": [hit],
            "parent_groups": [_group("P", children, binding=[img])],
        }
        payload = build_evidence_groups_payload(data)
        media = payload["groups"][0]["media"]
        assert len(media) == 1
        assert media[0]["primary_reason"] == "figure_binding"
        assert "图5" in media[0]["figure_ids"]

    def test_case7_same_parent_image_is_p3(self):
        img = _img_child("img1", 2, "asset-a", caption="示意图说明。")
        children = [_child("c1", 1), img, _child("c3", 3)]
        data = {
            "results": [_hit("c1", "P", chunk_index=1)],
            "parent_groups": [_group("P", children)],
        }
        payload = build_evidence_groups_payload(data)
        media = payload["groups"][0]["media"]
        assert len(media) == 1
        assert media[0]["primary_reason"] == "same_parent"
        assert media[0]["asset_id"] == "asset-a"


# ──────────────────────────────────────────────────────────────────────
# Global dedup / evidence merge / multi-group ownership
# ──────────────────────────────────────────────────────────────────────

def _candidate(reason, group_index, *, asset_key="asset-a", chunk_id="img1", group_score=0.5, binding_score=0.0, hit_ratio=0.0, figure_ids=None):
    chunk = {"chunk_id": chunk_id, "chunk_type": "image", "metadata": {"asset_id": asset_key}}
    return _new_media_candidate(
        chunk,
        reason,
        group_index,
        group_score=group_score,
        binding_score=binding_score,
        figure_ids=figure_ids,
        hit_ratio=hit_ratio,
    )


class TestEvidenceAssignMedia:
    def test_case8_same_parent_tie_break_prefers_higher_hit_ratio(self):
        candidates = [
            _candidate("same_parent", 0, hit_ratio=0.2),
            _candidate("same_parent", 1, hit_ratio=0.8),
        ]
        assigned = _evidence_assign_media(candidates)
        winner = assigned["asset-a"]
        assert winner["group_index"] == 1  # higher hit_ratio wins

    def test_case8b_figure_binding_beats_same_parent_even_with_high_hit_ratio(self):
        candidates = [
            _candidate("same_parent", 0, hit_ratio=1.0),
            _candidate("figure_binding", 1, binding_score=0.9, figure_ids=["图5"]),
        ]
        assigned = _evidence_assign_media(candidates)
        winner = assigned["asset-a"]
        assert winner["primary_reason"] == "figure_binding"
        assert winner["group_index"] == 1

    def test_case9_direct_hit_wins_and_all_evidence_merged(self):
        candidates = [
            _candidate("same_parent", 0),
            _candidate("figure_binding", 0, binding_score=0.9, figure_ids=["图5"]),
            _candidate("direct_hit", 0, group_score=0.95),
        ]
        assigned = _evidence_assign_media(candidates)
        winner = assigned["asset-a"]
        assert winner["primary_reason"] == "direct_hit"
        assert winner["relation_sources"] == ["direct_hit", "figure_binding", "same_parent"]
        assert "图5" in winner["figure_ids"]

    def test_case10_same_image_across_groups_returned_once_with_related_group(self):
        candidates = [
            _candidate("direct_hit", 0),
            _candidate("figure_binding", 1, binding_score=0.9, figure_ids=["图5"]),
        ]
        assigned = _evidence_assign_media(candidates)
        assert len(assigned) == 1
        winner = assigned["asset-a"]
        assert winner["group_index"] == 0
        assert winner["related_group_indices"] == [1]

    def test_case11_same_asset_different_chunk_ids_dedup_to_one(self):
        candidates = [
            _candidate("direct_hit", 0, asset_key="asset-a", chunk_id="img1", group_score=0.9),
            _candidate("direct_hit", 1, asset_key="asset-a", chunk_id="img1-vlm", group_score=0.7),
        ]
        assigned = _evidence_assign_media(candidates)
        assert len(assigned) == 1  # one asset → one image entity


# ──────────────────────────────────────────────────────────────────────
# table / equation safety
# ──────────────────────────────────────────────────────────────────────

class TestEvidenceTableEquationSafety:
    def test_case12_table_and_equation_not_dropped_by_image_dedup(self):
        table = _hit("t1", "P", chunk_index=1, chunk_type="table", score=0.85)
        eq = _hit("e1", "P", chunk_index=2, chunk_type="equation", score=0.7)
        img = _img_child("img1", 3, "asset-a", caption="示意图说明。")
        children = [table, eq, img]
        data = {
            "results": [table, eq],
            "parent_groups": [_group("P", children)],
        }
        payload = build_evidence_groups_payload(data)
        group = payload["groups"][0]
        chunk_ids = [c["chunk_id"] for c in group["chunks"]]
        assert "t1" in chunk_ids
        assert "e1" in chunk_ids
        # image is media, not a chunk; dedup did not delete table/equation
        media_ids = [m["chunk_id"] for m in group["media"]]
        assert "img1" in media_ids


# ──────────────────────────────────────────────────────────────────────
# Image content field label (上下文：) must not be stripped for images
# ──────────────────────────────────────────────────────────────────────

class TestImageContextLabelPreserved:
    def test_clean_content_keeps_context_for_image_but_strips_for_text(self):
        content = "章节：王羲之 草书寒切帖卷东晋\n图片标题：PDF 第 1 页主视觉\n上下文：失彩家月半帖星一"
        # image keeps 上下文
        assert "上下文" in _cloud_clean_content(content, kind="image")
        # text strips it as a wrapper label
        assert "上下文" not in _cloud_clean_content(content, kind="text")

    def test_image_with_ocr_context_is_not_low_information(self):
        img = _img_child_with_context("img1", 2, "asset-a")
        assert _cloud_is_low_information_chunk(img) is False

    def test_same_parent_image_with_ocr_context_returned_as_media(self):
        img = _img_child_with_context("img1", 2, "asset-a")
        children = [_child("c1", 1), img, _child("c3", 3)]
        data = {
            "results": [_hit("c1", "P", chunk_index=1)],
            "parent_groups": [_group("P", children)],
        }
        payload = build_evidence_groups_payload(data)
        media = payload["groups"][0]["media"]
        assert len(media) == 1
        assert media[0]["asset_id"] == "asset-a"
        assert media[0]["primary_reason"] == "same_parent"
        assert "上下文" in media[0]["content"]

    def test_empty_placeholder_image_still_filtered(self):
        # A placeholder image with no VLM/OCR content remains low-information.
        img = _img_child("img1", 2, "asset-a")  # content_preview == ""
        assert _cloud_is_low_information_chunk(img) is True


# ──────────────────────────────────────────────────────────────────────
# Response contract: flat arrays are homogeneous, no redundant fields
# ──────────────────────────────────────────────────────────────────────

class TestEvidenceGroupsContract:
    def _payload_with_image(self):
        img = _img_child_with_context("img1", 2, "asset-a")
        children = [_child("c1", 1), img, _child("c3", 3)]
        return build_evidence_groups_payload(
            {
                "results": [_hit("c1", "P", chunk_index=1)],
                "parent_groups": [_group("P", children)],
            }
        )

    def test_no_empty_context_text_placeholder(self):
        payload = self._payload_with_image()
        assert "context_text" not in payload

    def test_flat_chunks_are_homogeneous_chunk_payloads(self):
        payload = self._payload_with_image()
        assert payload["chunks"]
        assert all("direct_hit" in c and "primary_reason" not in c for c in payload["chunks"])

    def test_flat_media_are_homogeneous_media_payloads(self):
        payload = self._payload_with_image()
        assert len(payload["media"]) == 1
        assert all("primary_reason" in m and "direct_hit" not in m for m in payload["media"])

    def test_no_redundant_or_misleading_fields(self):
        payload = self._payload_with_image()
        assert "dedup_key" not in payload["media"][0]
        assert "original_rank" not in payload["chunks"][0]
        group = payload["groups"][0]
        assert "hit_count" not in group
        assert "direct_hit_count" in group


# ──────────────────────────────────────────────────────────────────────
# Pydantic response schema (machine-readable contract)
# ──────────────────────────────────────────────────────────────────────

class TestEvidenceGroupsSchema:
    def test_payload_validates_against_response_model(self):
        img = _img_child_with_context("img1", 2, "asset-a")
        children = [_child("c1", 1), img, _child("c3", 3)]
        payload = build_evidence_groups_payload(
            {
                "results": [_hit("c1", "P", chunk_index=1)],
                "parent_groups": [_group("P", children)],
            }
        )
        model = EvidenceGroupsResponse.model_validate(payload)
        assert model.meta.schema_version == "evidence_groups.v1"
        assert model.return_mode == "evidence_groups"
        assert model.groups[0].chunks[0].direct_hit is True
        assert model.groups[0].media[0].primary_reason == "same_parent"
        # Flat media mirrors the group-level media for a single group.
        assert len(model.media) == 1
        assert model.media[0].chunk_id == model.groups[0].media[0].chunk_id

    def test_flat_arrays_round_trip_to_typed_models(self):
        payload = build_evidence_groups_payload(
            {
                "results": [_hit("c1", "P", chunk_index=1)],
                "parent_groups": [_group("P", [_child("c1", 1)])],
            }
        )
        model = EvidenceGroupsResponse.model_validate(payload)
        assert isinstance(model.chunks[0].chunk_id, str)
        assert model.groups[0].chunks[0].retrieval_score is not None


class TestDocumentUrl:
    def test_document_url_on_group_not_items(self):
        doc_id = "5d7cdf37-360b-471c-9a6e-6b1aa985e637"
        img = _img_child_with_context("img1", 2, "asset-a")
        img["document_id"] = doc_id
        children = [
            {**_child("c1", 1), "document_id": doc_id},
            img,
            _child("c3", 3),
        ]
        payload = build_evidence_groups_payload(
            {
                "results": [{**_hit("c1", "P", chunk_index=1), "document_id": doc_id}],
                "parent_groups": [_group("P", children)],
            }
        )
        suffix = f"/documents/{doc_id}/content"
        # 主文档链接只在 group 级，一个 group 一个。
        assert payload["groups"][0]["document_id"] == doc_id
        assert payload["groups"][0]["document_url"].endswith(suffix)
        # 条目级只保留 document_id，不再重复 document_url。
        assert payload["chunks"][0]["document_id"] == doc_id
        assert "document_url" not in payload["chunks"][0]
        assert payload["media"][0]["document_id"] == doc_id
        assert "document_url" not in payload["media"][0]

    def test_group_document_url_empty_without_document_id(self):
        img = _img_child_with_context("img1", 2, "asset-a")
        children = [_child("c1", 1), img, _child("c3", 3)]
        payload = build_evidence_groups_payload(
            {
                "results": [_hit("c1", "P", chunk_index=1)],
                "parent_groups": [_group("P", children)],
            }
        )
        assert payload["groups"][0]["document_id"] == ""
        assert payload["groups"][0]["document_url"] == ""
        assert payload["chunks"][0]["document_id"] == ""
        assert "document_url" not in payload["chunks"][0]
        assert payload["media"][0]["document_id"] == ""
        assert "document_url" not in payload["media"][0]
