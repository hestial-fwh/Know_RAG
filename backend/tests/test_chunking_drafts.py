from app.services.chunking.chunk_draft import ChildDraft, ChunkDraft, ParentDraft


def test_chunk_draft_refs_and_links():
    parent = ParentDraft(content="parent")
    child = ChildDraft(content="child", parent_ref=parent.draft_ref)
    generic = ChunkDraft(content="generic", prev_ref=parent.draft_ref, next_ref=child.draft_ref)

    assert parent.draft_ref.startswith("parent:")
    assert child.draft_ref.startswith("child:")
    assert child.parent_ref == parent.draft_ref
    assert generic.prev_ref == parent.draft_ref
    assert generic.next_ref == child.draft_ref
