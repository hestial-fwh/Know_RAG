import json
from types import SimpleNamespace
from unittest.mock import MagicMock

from app.db.models import Document, DocumentCanonicalArtifact
from app.services.document_relation.page_layout_graph import (
    PageLayoutGraph,
    PageLayoutNode,
    PageLayoutPage,
)
from app.services.document_service import get_document_visual_layout
from app.services.document_visual_item_resolver import (
    resolve_document_visual_layout,
)
from scripts.document_profile_dataset.generator import generate_dataset
from scripts.document_profile_dataset.replay import replay_manifest


EXPECTED_ITEM_COUNTS = {
    "VI-01": 2,
    "VI-02": 2,
    "VI-03": 4,
    "VI-04": 4,
    "VI-05": 1,
    "VI-06": 2,
    "VI-07": 2,
    "VI-08": 1,
    "VI-09": 1,
    "VI-10": 2,
}


def _layout_pages(snapshot: dict) -> dict[int, dict]:
    stats = snapshot["layers"]["canonical_artifact"]["stats_json"]
    layout = stats["document_visual_layout"]
    return {
        int(page["page_no"]): page
        for page in layout["pages"]
    }


def test_dcp11_vi_cases_produce_expected_visual_item_counts(tmp_path):
    manifest = generate_dataset(tmp_path, seed=111)
    snapshots = replay_manifest(tmp_path, manifest)
    layout_document = next(
        document
        for document in manifest["documents"]
        if document["document_id"] == "dcp-adversarial-layout"
    )
    snapshot = next(
        item
        for item in snapshots
        if item["document_id"] == layout_document["document_id"]
    )
    pages = _layout_pages(snapshot)
    gold = json.loads(
        (tmp_path / layout_document["gold_path"]).read_text(encoding="utf-8")
    )

    for gold_page in gold["pages"]:
        case_id = gold_page["case_id"]
        assert len(pages[int(gold_page["page_no"])]["visual_items"]) == (
            EXPECTED_ITEM_COUNTS[case_id]
        )


def test_dcp11_layout_guards_are_conservative(tmp_path):
    manifest = generate_dataset(tmp_path, seed=113)
    snapshots = replay_manifest(tmp_path, manifest)
    snapshot = next(
        item
        for item in snapshots
        if item["document_id"] == "dcp-adversarial-layout"
    )
    pages = _layout_pages(snapshot)

    vi02 = pages[2]
    assert any(
        relation["status"] == "ambiguous"
        for relation in vi02["text_relations"]
    )

    vi06 = pages[6]
    assert vi06["summary"]["duplicate_fallback_count"] == 1

    vi07 = pages[7]
    assert vi07["fallback_reason"] == "full_page_raster_split_candidates"
    assert {item["status"] for item in vi07["visual_items"]} == {"candidate"}

    vi08 = pages[8]
    assert vi08["fallback_reason"] == "no_reliable_internal_partition"
    assert vi08["visual_items"][0]["status"] == "unsegmented_page_visual"

    vi09 = pages[9]
    assert all(
        relation["status"] == "unbound"
        for relation in vi09["text_relations"]
    )

    vi07_image_chunks = [
        chunk
        for chunk in snapshot["layers"]["chunk_metadata"]
        if chunk["chunk_type"] == "image"
        and (chunk.get("metadata_json") or {}).get("page_no") == 7
    ]
    assert len(vi07_image_chunks) == 1
    vi07_chunk_metadata = vi07_image_chunks[0]["metadata_json"]
    assert len(vi07_chunk_metadata["visual_item_ids"]) == 2
    assert "visual_item_id" not in vi07_chunk_metadata


def test_dcp11_same_seed_layout_is_deterministic(tmp_path):
    layouts = []
    for folder in ("first", "second"):
        root = tmp_path / folder
        manifest = generate_dataset(root, seed=127)
        snapshots = replay_manifest(root, manifest)
        snapshot = next(
            item
            for item in snapshots
            if item["document_id"] == "dcp-adversarial-layout"
        )
        layouts.append(
            snapshot["layers"]["canonical_artifact"]["stats_json"][
                "document_visual_layout"
            ]
        )

    assert layouts[0] == layouts[1]


def test_dcp11_visual_layout_api_returns_layout_and_compact_assets():
    document = SimpleNamespace(id="doc-1")
    artifact = SimpleNamespace(
        parse_revision="revision-1",
        stats_json={
            "document_visual_layout": {
                "algorithm_version": "document-visual-items-dcp11-v1",
                "shadow_only": True,
                "pages": [
                    {
                        "page_no": 1,
                        "extent_source": "authoritative",
                        "fallback_reason": "",
                        "summary": {"visual_item_count": 1},
                        "regions": [],
                        "visual_items": [
                            {
                                "visual_item_id": "visual-1",
                                "asset_ids": ["asset-1"],
                            }
                        ],
                        "text_relations": [],
                    }
                ],
            }
        },
        assets_json={
            "items": [
                {
                    "asset_id": "asset-1",
                    "page_no": 1,
                    "caption": "作品一",
                    "image_url": "/files/asset-1.png",
                    "image_path": "asset-1.png",
                    "bbox": [0, 0, 100, 100],
                    "metadata": {"visual_role": "figure"},
                    "ocr_text": "must-not-leak-through-this-api",
                }
            ]
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
    index_response = get_document_visual_layout(db, "doc-1")
    response = get_document_visual_layout(db, "doc-1", page_no=1)

    assert index_response["data"]["layout"]["pages"] == []
    assert index_response["data"]["available_pages"][0]["page_no"] == 1
    assert response["success"] is True
    assert response["data"]["available"] is True
    assert response["data"]["shadow_only"] is True
    assert response["data"]["assets"]["asset-1"]["caption"] == "作品一"
    assert "ocr_text" not in response["data"]["assets"]["asset-1"]


def test_dcp11_derived_extent_does_not_make_a_local_figure_full_page():
    node = PageLayoutNode(
        node_id="node-1",
        node_kind="asset",
        page_no=1,
        source_order=1,
        bbox=(100.0, 100.0, 300.0, 300.0),
        normalized_bbox=(0.0, 0.0, 1.0, 1.0),
        semantic_role="figure",
        asset_id="asset-1",
    )
    graph = PageLayoutGraph(
        pages={
            1: PageLayoutPage(
                page_no=1,
                extent=(0.0, 0.0, 300.0, 300.0),
                extent_source="derived",
                nodes=[node],
            )
        }
    )

    layout = resolve_document_visual_layout(
        graph,
        assets={"asset-1": SimpleNamespace(metadata={})},
    ).to_metadata()
    page = layout["pages"][0]

    assert page["fallback_reason"] == ""
    assert page["visual_items"][0]["status"] == "resolved"


def test_dcp11_overlapping_full_page_assets_collapse_to_one_item():
    nodes = [
        PageLayoutNode(
            node_id=f"node-{index}",
            node_kind="asset",
            page_no=1,
            source_order=index,
            bbox=(0.0, 0.0, 600.0, 800.0),
            normalized_bbox=(0.0, 0.0, 1.0, 1.0),
            semantic_role="page_primary_visual",
            source_block_id=f"block-{index}",
            asset_id=f"asset-{index}",
        )
        for index in (1, 2)
    ]
    graph = PageLayoutGraph(
        pages={
            1: PageLayoutPage(
                page_no=1,
                extent=(0.0, 0.0, 600.0, 800.0),
                extent_source="authoritative",
                nodes=nodes,
            )
        }
    )

    page = resolve_document_visual_layout(
        graph,
        assets={
            "asset-1": SimpleNamespace(metadata={}),
            "asset-2": SimpleNamespace(metadata={}),
        },
    ).to_metadata()["pages"][0]

    assert len(page["visual_items"]) == 1
    assert set(page["visual_items"][0]["asset_ids"]) == {
        "asset-1",
        "asset-2",
    }
    assert "overlapping_assets_collapsed" in page["visual_items"][0]["evidence"]


def test_dcp11_short_body_text_inside_visual_is_not_force_bound():
    nodes = [
        PageLayoutNode(
            node_id="asset-node",
            node_kind="asset",
            page_no=1,
            source_order=1,
            bbox=(100.0, 100.0, 500.0, 700.0),
            normalized_bbox=(0.166, 0.125, 0.833, 0.875),
            semantic_role="figure",
            asset_id="asset-1",
        ),
        PageLayoutNode(
            node_id="text-node",
            node_kind="text",
            page_no=1,
            source_order=2,
            bbox=(150.0, 200.0, 450.0, 260.0),
            normalized_bbox=(0.25, 0.25, 0.75, 0.325),
            semantic_role="body_text",
            source_block_id="body-1",
            text="This short paragraph is ordinary body text.",
        ),
    ]
    graph = PageLayoutGraph(
        pages={
            1: PageLayoutPage(
                page_no=1,
                extent=(0.0, 0.0, 600.0, 800.0),
                extent_source="authoritative",
                nodes=nodes,
            )
        }
    )

    relation = resolve_document_visual_layout(
        graph,
        assets={"asset-1": SimpleNamespace(metadata={})},
    ).to_metadata()["pages"][0]["text_relations"][0]

    assert relation["status"] == "unbound"
    assert relation["relation_type"] == "unbound"
    assert relation["visual_item_ids"] == []
    assert "semantic_role_not_bindable" in relation["evidence"]
