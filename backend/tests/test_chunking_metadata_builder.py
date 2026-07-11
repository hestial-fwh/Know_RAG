from app.services.chunking.metadata_builder import (
    build_image_child_metadata,
    build_parent_metadata,
    build_table_child_metadata,
    build_text_child_metadata,
    mark_skip_embedding,
)


def test_parent_and_child_metadata_builders():
    section = {"strategy": "heading", "heading_path": ["A"], "blocks": [], "page_start": 1, "page_end": 2}
    parent = build_parent_metadata(section=section, section_index=0, file_type="pdf", child_types=["text"])
    text = build_text_child_metadata(parent_id="p1", parent_context="A", section=section, block_meta={"block_order": 3})
    table = build_table_child_metadata(parent_id="p1", parent_context="A", section=section, block_meta={"block_order": 4})
    image = build_image_child_metadata(parent_id="p1", parent_context="A", section=section, block_meta={"block_order": 5})

    assert parent["chunk_role"] == "parent"
    assert parent["parser"] == "structured_parent_child"
    assert text["chunk_role"] == "child"
    assert text["parent_chunk_id"] == "p1"
    assert table["content_kind"] == "table"
    assert image["normalized_block_type"] == "image"
    assert mark_skip_embedding({})["skip_embedding_recommended"] is True
