from app.services.chunking.chunk_draft import ChildDraft, ParentDraft
from app.services.chunking.chunk_linker import ChunkLinker


def test_linker_sets_parent_and_neighbor_refs_stably():
    p1 = ParentDraft(content="p1")
    p2 = ParentDraft(content="p2")
    c1 = ChildDraft(content="c1", chunk_type="text")
    c2 = ChildDraft(content="c2", chunk_type="table")
    c3 = ChildDraft(content="c3", chunk_type="image")

    ordered = ChunkLinker.link_parent_child_and_neighbors(
        [p1, p2],
        {p1.draft_ref: [c1, c2], p2.draft_ref: [c3]},
    )

    assert [draft.chunk_index for draft in ordered] == list(range(len(ordered)))
    assert p1.next_ref == p2.draft_ref
    assert c1.parent_ref == p1.draft_ref
    assert c3.parent_ref == p2.draft_ref
    assert c1.next_ref == c2.draft_ref
    assert c2.next_ref == c3.draft_ref
    assert c3.prev_ref == c2.draft_ref
