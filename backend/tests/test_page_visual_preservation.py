import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from app.services.document_parser.canonical_document import (
    CanonicalAsset,
    build_canonical_document,
)
from app.services.document_parser.mineru_adapter import (
    _content_list_to_blocks,
    _find_content_list,
)
from app.services.document_parser.page_visual_enricher import (
    _ordered_blocks,
    attach_pdf_page_visual_profile,
    enrich_pdf_page_visuals,
)
from app.services.document_parser.page_visual_profile import profile_pdf_pages
from app.services.document_parser.pdf_page_renderer import render_pdf_pages
from app.services.document_parser.types import ParsedBlock, ParsedDocument
from app.services.document_relation.asset_group_resolver import resolve_asset_groups
from app.services.document_relation.page_layout_graph import build_page_layout_graph
from app.services.parser_service import parse_local_document
from scripts.document_profile_dataset.generator import generate_dataset


def _parsed_from_raw(path: Path, document_id: str) -> ParsedDocument:
    payload = json.loads(path.read_text(encoding="utf-8"))
    items = _find_content_list(payload)
    for item in items:
        item.pop("img_path", None)
    blocks = _content_list_to_blocks(
        items,
        {},
        knowledge_base_id="dcp1-kb",
        document_id=document_id,
    )
    return ParsedDocument(
        content="\n\n".join(block.index_text() for block in blocks),
        blocks=blocks,
        metadata={"parser": "mineru"},
    )


def _fake_renderer(path, page_numbers, **kwargs):
    return {
        page_no: {
            "path": f"minio://media/page-{page_no}.jpg",
            "url": f"/files/media/page-{page_no}.jpg",
            "relative_path": f"media/page-{page_no}.jpg",
            "filename": f"page-{page_no}.jpg",
            "sha256": f"sha-{page_no}",
            "size": 100 + page_no,
            "base64": "must-not-be-retained",
        }
        for page_no in page_numbers
    }


def test_dcp1_profiles_all_dcp0_pages_against_visual_gold(tmp_path):
    manifest = generate_dataset(tmp_path, seed=101)

    observed = {}
    for document in manifest["documents"]:
        parsed = _parsed_from_raw(
            tmp_path / document["raw_response_path"],
            document["document_id"],
        )
        profiles = profile_pdf_pages(tmp_path / document["pdf_path"], parsed)
        observed.update(
            {
                (document["document_id"], profile.page_no): profile.decision
                for profile in profiles
            }
        )
        gold = json.loads(
            (tmp_path / document["gold_path"]).read_text(encoding="utf-8")
        )
        for page in gold["pages"]:
            decision = observed[(document["document_id"], page["page_no"])]
            if page["preserve_page_visual"]:
                expected = (
                    "preserve"
                    if "missing_page_primary_visual"
                    in page.get("expected_known_gaps", [])
                    else "already_present"
                )
                assert decision == expected
            else:
                assert decision == "normal"


def test_dcp1_enrichment_closes_missing_visuals_without_duplicates(tmp_path):
    manifest = generate_dataset(tmp_path, seed=103)
    injected_count = 0
    preserved_pages = 0

    for document in manifest["documents"]:
        parsed = _parsed_from_raw(
            tmp_path / document["raw_response_path"],
            document["document_id"],
        )
        enriched = enrich_pdf_page_visuals(
            tmp_path / document["pdf_path"],
            parsed,
            knowledge_base_id="dcp1-kb",
            document_id=document["document_id"],
            renderer=_fake_renderer,
        )
        injected_count += int(enriched.metadata["page_visual_asset_count"])
        gold = json.loads(
            (tmp_path / document["gold_path"]).read_text(encoding="utf-8")
        )
        for page in gold["pages"]:
            if not page["preserve_page_visual"]:
                continue
            preserved_pages += 1
            page_blocks = [
                block
                for block in enriched.blocks
                if block.page_no == page["page_no"] and block.block_type == "image"
            ]
            full_page_blocks = [
                block
                for block in page_blocks
                if (block.metadata or {}).get("visual_role") == "page_primary_visual"
                or (
                    block.bbox
                    and (block.bbox[2] - block.bbox[0])
                    * (block.bbox[3] - block.bbox[1])
                    >= 0.65 * 612 * 792
                )
            ]
            assert len(full_page_blocks) == 1
        canonical = build_canonical_document(
            enriched,
            file_type="pdf",
            document_title=document["document_id"],
        )
        primary_assets = [
            asset
            for asset in canonical.assets.values()
            if asset.metadata.get("visual_role") == "page_primary_visual"
        ]
        injected_assets = [
            asset
            for asset in primary_assets
            if asset.metadata.get("page_visual_status") == "preserved"
        ]
        assert len(injected_assets) == enriched.metadata["page_visual_asset_count"]
        assert all(
            not block.image_base64
            for block in enriched.blocks
            if (block.metadata or {}).get("page_visual_status") == "preserved"
        )
        assert len(primary_assets) == sum(
            bool(page["preserve_page_visual"]) for page in gold["pages"]
        )

    assert preserved_pages == 6
    assert injected_count == 4


def test_page_visual_insertion_preserves_page_less_block_order():
    existing = [
        ParsedBlock(block_type="text", text="cover", order_index=0),
        ParsedBlock(block_type="text", text="page one", page_no=1, order_index=1),
        ParsedBlock(block_type="text", text="interlude", order_index=2),
        ParsedBlock(block_type="text", text="page two", page_no=2, order_index=3),
    ]
    injected = {
        1: ParsedBlock(block_type="image", text="visual one", page_no=1),
        3: ParsedBlock(block_type="image", text="visual three", page_no=3),
    }

    ordered = _ordered_blocks(existing, injected)

    assert [block.text for block in ordered] == [
        "cover",
        "page one",
        "visual one",
        "interlude",
        "page two",
        "visual three",
    ]
    assert [block.order_index for block in ordered] == list(range(len(ordered)))


def test_dcp1_normal_academic_page_is_not_rendered(tmp_path):
    manifest = generate_dataset(tmp_path, seed=107)
    academic = next(
        item for item in manifest["documents"] if item["document_type"] == "academic"
    )
    parsed = _parsed_from_raw(
        tmp_path / academic["raw_response_path"],
        academic["document_id"],
    )
    calls = []

    def renderer(path, page_numbers, **kwargs):
        calls.append(list(page_numbers))
        return {}

    enriched = enrich_pdf_page_visuals(
        tmp_path / academic["pdf_path"],
        parsed,
        knowledge_base_id="dcp1-kb",
        document_id=academic["document_id"],
        renderer=renderer,
    )

    assert calls == [[]]
    assert enriched.metadata["page_visual_asset_count"] == 0
    assert enriched.metadata["page_visual_profile"]["decision_counts"] == {"normal": 1}


def test_dcp2_read_only_profile_collects_all_pdf_pages_without_rendering(tmp_path):
    manifest = generate_dataset(tmp_path, seed=111)
    mixed = next(
        item for item in manifest["documents"] if item["document_type"] == "mixed"
    )
    parsed = _parsed_from_raw(
        tmp_path / mixed["raw_response_path"],
        mixed["document_id"],
    )

    profiled = attach_pdf_page_visual_profile(
        tmp_path / mixed["pdf_path"],
        parsed,
    )

    assert profiled.metadata["page_visual_profile_collected"] is True
    assert profiled.metadata["page_visual_preservation_enabled"] is False
    assert profiled.metadata["page_visual_profile"]["page_count"] == 3
    assert len(profiled.metadata["page_visual_profile"]["decisions"]) == 3
    assert len(profiled.blocks) == len(parsed.blocks)
    assert not any(
        (block.metadata or {}).get("visual_role") == "page_primary_visual"
        for block in profiled.blocks
    )


def test_dcp1_marks_existing_full_page_parser_image_as_primary_visual(tmp_path):
    manifest = generate_dataset(tmp_path, seed=108)
    visual = manifest["documents"][0]
    parsed = _parsed_from_raw(
        tmp_path / visual["raw_response_path"],
        visual["document_id"],
    )

    enriched = enrich_pdf_page_visuals(
        tmp_path / visual["pdf_path"],
        parsed,
        knowledge_base_id="dcp1-kb",
        document_id=visual["document_id"],
        renderer=_fake_renderer,
    )

    existing = next(
        block
        for block in enriched.blocks
        if block.page_no == 2 and block.block_type == "image"
    )
    assert existing.metadata["visual_role"] == "page_primary_visual"
    assert existing.metadata["page_visual_status"] == "already_present"
    assert existing.metadata["exclude_from_asset_group"] is True


def test_dcp1_renderer_failure_is_fail_open(tmp_path):
    manifest = generate_dataset(tmp_path, seed=109)
    visual = manifest["documents"][0]
    parsed = _parsed_from_raw(
        tmp_path / visual["raw_response_path"],
        visual["document_id"],
    )
    original_block_count = len(parsed.blocks)

    def failing_renderer(*args, **kwargs):
        raise RuntimeError("render unavailable")

    enriched = enrich_pdf_page_visuals(
        tmp_path / visual["pdf_path"],
        parsed,
        knowledge_base_id="dcp1-kb",
        document_id=visual["document_id"],
        renderer=failing_renderer,
    )

    assert len(enriched.blocks) == original_block_count
    assert enriched.metadata["page_visual_preservation_degraded"] is True
    assert "render unavailable" in enriched.metadata["page_visual_preservation_error"]


def test_selected_page_renderer_renders_only_requested_page(tmp_path):
    manifest = generate_dataset(tmp_path, seed=113)
    mixed = next(
        item for item in manifest["documents"] if item["document_type"] == "mixed"
    )
    saved = []

    def save_media(**kwargs):
        saved.append(kwargs)
        return {
            "path": "minio://page.jpg",
            "url": "/files/page.jpg",
            "relative_path": "page.jpg",
            "filename": kwargs["filename_hint"],
            "sha256": "sha",
            "size": len(kwargs["data"]),
            "base64": "encoded-page",
        }

    with patch(
        "app.services.document_parser.pdf_page_renderer.save_media_bytes",
        side_effect=save_media,
    ):
        rendered = render_pdf_pages(
            tmp_path / mixed["pdf_path"],
            [2],
            knowledge_base_id="dcp1-kb",
            document_id=mixed["document_id"],
            dpi=96,
        )

    assert list(rendered) == [2]
    assert len(saved) == 1
    assert saved[0]["filename_hint"] == "pdf_page_primary_visual_2.jpg"
    assert saved[0]["data"].startswith(b"\xff\xd8")
    assert "base64" not in rendered[2]


def test_selected_page_renderer_keeps_other_pages_when_one_save_fails(tmp_path):
    manifest = generate_dataset(tmp_path, seed=127)
    mixed = next(
        item for item in manifest["documents"] if item["document_type"] == "mixed"
    )
    saved_pages = []

    def save_media(**kwargs):
        page_no = int(kwargs["filename_hint"].rsplit("_", 1)[-1].split(".", 1)[0])
        if page_no == 2:
            raise RuntimeError("page 2 storage unavailable")
        saved_pages.append(page_no)
        return {
            "path": f"minio://page-{page_no}.jpg",
            "url": f"/files/page-{page_no}.jpg",
            "relative_path": f"page-{page_no}.jpg",
            "filename": kwargs["filename_hint"],
            "sha256": f"sha-{page_no}",
            "size": len(kwargs["data"]),
            "base64": "",
        }

    with patch(
        "app.services.document_parser.pdf_page_renderer.save_media_bytes",
        side_effect=save_media,
    ):
        rendered = render_pdf_pages(
            tmp_path / mixed["pdf_path"],
            [1, 2, 3],
            knowledge_base_id="dcp1-kb",
            document_id=mixed["document_id"],
            dpi=96,
        )

    assert list(rendered) == [1, 3]
    assert saved_pages == [1, 3]


def test_page_primary_visual_is_excluded_from_relation_asset_group():
    extent = {
        "page_extent": [0, 0, 1000, 1000],
        "page_extent_source": "authoritative",
        "section_path": "S",
    }
    assets = {
        "page": CanonicalAsset(
            asset_id="page",
            page_no=1,
            bbox=(0, 0, 1000, 1000),
            metadata={
                **extent,
                "visual_role": "page_primary_visual",
                "exclude_from_asset_group": True,
            },
        ),
        "left": CanonicalAsset(
            asset_id="left",
            page_no=1,
            bbox=(50, 200, 470, 700),
            metadata=extent,
        ),
        "right": CanonicalAsset(
            asset_id="right",
            page_no=1,
            bbox=(530, 200, 950, 700),
            metadata=extent,
        ),
    }

    layout = build_page_layout_graph(assets=assets, provenance_nodes=[])
    groups = resolve_asset_groups(layout)

    assert len(groups) == 1
    assert groups[0].member_asset_ids == ("left", "right")


@pytest.mark.parametrize("media_enabled", [True, False])
def test_parser_entrypoint_honors_global_and_document_media_switches(
    monkeypatch,
    tmp_path,
    media_enabled,
):
    source = tmp_path / "sample.pdf"
    source.write_bytes(b"%PDF-placeholder")
    document = SimpleNamespace(
        id="dcp1-doc",
        knowledge_base_id="dcp1-kb",
        file_type="pdf",
        metadata_json={
            "parse_options": {
                "parser_engine": "mineru",
                "enable_media_crop": media_enabled,
            }
        },
    )
    parsed = ParsedDocument(
        content="body",
        blocks=[ParsedBlock(block_type="text", text="body", page_no=1)],
        metadata={},
    )
    enriched = ParsedDocument(content="enriched", metadata={"enriched": True})
    profiled = ParsedDocument(content="profiled", metadata={"profiled": True})
    config = SimpleNamespace(
        backend="pipeline",
        parse_method="auto",
        base_url="http://mineru",
    )
    monkeypatch.setattr(
        "app.services.parser_service.ensure_local_document_file",
        lambda document: source,
    )
    monkeypatch.setattr(
        "app.services.parser_service.resolve_parser_engine",
        lambda *args, **kwargs: "mineru",
    )
    monkeypatch.setattr(
        "app.services.parser_service.get_mineru_pdf_config",
        lambda *args, **kwargs: config,
    )
    monkeypatch.setattr(
        "app.services.parser_service.parse_document_with_mineru",
        lambda *args, **kwargs: parsed,
    )
    preserve = patch(
        "app.services.parser_service.enrich_pdf_page_visuals",
        return_value=enriched,
    )
    profile = patch(
        "app.services.parser_service.attach_pdf_page_visual_profile",
        return_value=profiled,
    )
    monkeypatch.setattr(
        "app.services.parser_service.settings.document_page_visual_preservation_enabled",
        True,
    )

    with preserve as preserve_mock, profile as profile_mock:
        result = parse_local_document(document)

    assert result is (enriched if media_enabled else profiled)
    assert preserve_mock.called is media_enabled
    assert profile_mock.called is not media_enabled


def test_parser_entrypoint_global_flag_defaults_to_fail_closed(monkeypatch, tmp_path):
    source = tmp_path / "sample.pdf"
    source.write_bytes(b"%PDF-placeholder")
    document = SimpleNamespace(
        id="dcp1-doc",
        knowledge_base_id="dcp1-kb",
        file_type="pdf",
        metadata_json={
            "parse_options": {
                "parser_engine": "mineru",
                "enable_media_crop": True,
            }
        },
    )
    parsed = ParsedDocument(content="body", metadata={})
    profiled = ParsedDocument(content="profiled", metadata={"profiled": True})
    config = SimpleNamespace(
        backend="pipeline",
        parse_method="auto",
        base_url="http://mineru",
    )
    monkeypatch.setattr(
        "app.services.parser_service.ensure_local_document_file",
        lambda document: source,
    )
    monkeypatch.setattr(
        "app.services.parser_service.resolve_parser_engine",
        lambda *args, **kwargs: "mineru",
    )
    monkeypatch.setattr(
        "app.services.parser_service.get_mineru_pdf_config",
        lambda *args, **kwargs: config,
    )
    monkeypatch.setattr(
        "app.services.parser_service.parse_document_with_mineru",
        lambda *args, **kwargs: parsed,
    )
    monkeypatch.setattr(
        "app.services.parser_service.settings.document_page_visual_preservation_enabled",
        False,
    )

    with (
        patch("app.services.parser_service.enrich_pdf_page_visuals") as preserve_mock,
        patch(
            "app.services.parser_service.attach_pdf_page_visual_profile",
            return_value=profiled,
        ) as profile_mock,
    ):
        result = parse_local_document(document)

    assert result is profiled
    preserve_mock.assert_not_called()
    profile_mock.assert_called_once()
