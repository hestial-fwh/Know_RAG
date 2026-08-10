"""Tests for document_relation.materializer, focusing on link_same_parent_media."""

import pytest

from app.services.document_relation.materializer import link_same_parent_media


class _FakeChunk:
    """Minimal chunk double for testing link_same_parent_media."""

    def __init__(self, chunk_id, *, parent_chunk_id=None, chunk_type="text",
                 chunk_index=0, relation_chunks=None):
        self.id = chunk_id
        self.parent_chunk_id = parent_chunk_id
        self.chunk_type = chunk_type
        self.chunk_index = chunk_index
        self.relation_chunks = list(relation_chunks or [])


class TestLinkSameParentMedia:
    """Verify same-parent text↔media auto-linking behaviour."""

    def test_basic_text_image_linking(self):
        """Text children and image children under the same parent are linked."""
        chunks = [
            _FakeChunk("t1", parent_chunk_id="p1", chunk_type="text", chunk_index=1),
            _FakeChunk("img1", parent_chunk_id="p1", chunk_type="image", chunk_index=2),
            _FakeChunk("t2", parent_chunk_id="p1", chunk_type="text", chunk_index=3),
        ]
        result = link_same_parent_media(chunks)

        t1 = result[0]
        img1 = result[1]
        t2 = result[2]

        assert "img1" in t1.relation_chunks
        assert "img1" in t2.relation_chunks
        assert "t1" in img1.relation_chunks
        assert "t2" in img1.relation_chunks

    def test_no_self_reference(self):
        """No chunk should link to itself."""
        chunks = [
            _FakeChunk("t1", parent_chunk_id="p1", chunk_type="text", chunk_index=1),
            _FakeChunk("img1", parent_chunk_id="p1", chunk_type="image", chunk_index=2),
        ]
        result = link_same_parent_media(chunks)
        assert "t1" not in result[0].relation_chunks  # text doesn't self-link
        assert "img1" not in result[1].relation_chunks  # image doesn't self-link

    def test_orphan_no_parent_unchanged(self):
        """Orphan chunks (no parent_chunk_id) are left unchanged."""
        chunks = [
            _FakeChunk("t1", parent_chunk_id=None, chunk_type="text", chunk_index=1),
            _FakeChunk("img1", parent_chunk_id=None, chunk_type="image", chunk_index=2),
        ]
        result = link_same_parent_media(chunks)
        assert result[0].relation_chunks == []
        assert result[1].relation_chunks == []

    def test_text_only_parent_no_change(self):
        """Parent with only text children: no media to link, no changes."""
        chunks = [
            _FakeChunk("t1", parent_chunk_id="p1", chunk_type="text", chunk_index=1),
            _FakeChunk("t2", parent_chunk_id="p1", chunk_type="text", chunk_index=2),
        ]
        result = link_same_parent_media(chunks)
        assert result[0].relation_chunks == []
        assert result[1].relation_chunks == []

    def test_preserves_existing_relation_chunks(self):
        """Existing relation_chunks are preserved, new links appended."""
        chunks = [
            _FakeChunk("t1", parent_chunk_id="p1", chunk_type="text",
                       chunk_index=1, relation_chunks=["existing_img"]),
            _FakeChunk("img1", parent_chunk_id="p1", chunk_type="image",
                       chunk_index=2, relation_chunks=["existing_text"]),
        ]
        result = link_same_parent_media(chunks)
        assert result[0].relation_chunks == ["existing_img", "img1"]
        assert result[1].relation_chunks == ["existing_text", "t1"]

    def test_table_also_linked(self):
        """Table children are treated as media alongside images."""
        chunks = [
            _FakeChunk("t1", parent_chunk_id="p1", chunk_type="text", chunk_index=1),
            _FakeChunk("tab1", parent_chunk_id="p1", chunk_type="table", chunk_index=2),
        ]
        result = link_same_parent_media(chunks)
        assert "tab1" in result[0].relation_chunks
        assert "t1" in result[1].relation_chunks

    def test_multiple_parents_independent(self):
        """Each parent group is processed independently."""
        chunks = [
            _FakeChunk("t1", parent_chunk_id="p1", chunk_type="text", chunk_index=1),
            _FakeChunk("img1", parent_chunk_id="p1", chunk_type="image", chunk_index=2),
            _FakeChunk("t2", parent_chunk_id="p2", chunk_type="text", chunk_index=3),
            _FakeChunk("img2", parent_chunk_id="p2", chunk_type="image", chunk_index=4),
        ]
        result = link_same_parent_media(chunks)

        t1 = result[0]
        img1 = result[1]
        t2 = result[2]
        img2 = result[3]

        # p1: t1 ↔ img1
        assert "img1" in t1.relation_chunks
        assert "t1" in img1.relation_chunks
        # No cross-parent contamination
        assert "img2" not in t1.relation_chunks
        assert "t2" not in img1.relation_chunks

        # p2: t2 ↔ img2
        assert "img2" in t2.relation_chunks
        assert "t2" in img2.relation_chunks
